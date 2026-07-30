# SPDX-FileCopyrightText: 2026 Reza Goharimehr <rgoharim@villanova.edu>
# SPDX-License-Identifier: Apache-2.0
"""Orchestrator: attach FMUs declared on a USD stage, step them, route USD<->FMI.

This is the Kit-side equivalent of ovfmi's FmiHost, but it drives a `pxr.Usd.Stage`
via omni.usd instead of ovstage. Per step:

    for each FmuInstance:
        read mapped 'input' USD attributes  -> FMU inputs
        do_step(dt)
        FMU outputs -> mapped 'output' USD attributes (and cached for coloring)
"""
from typing import Dict, List, Optional, Tuple

from .schema import FmuInstance, FmuMapping, parse_stage
from .fmu_runtime import FmuRuntime


def _get_stage():
    import omni.usd
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        raise RuntimeError("No USD stage is loaded.")
    return stage


def _instance_name(prim_path: str) -> str:
    return prim_path.strip("/").replace("/", "_") or "fmu"


class FmiUsdHost:
    def __init__(self, root: str = "/World", dt: float = 1.0 / 60.0):
        self.root = root
        self.dt = float(dt)
        self._runtimes: List[Tuple[FmuInstance, FmuRuntime]] = []
        self._outputs: Dict[Tuple[str, str], float] = {}   # (prim_path, usd_attr) -> value
        self._output_meta: Dict[Tuple[str, str], str] = {}  # (prim_path, usd_attr) -> fmu_attr
        self._time = 0.0
        self._log: List[str] = []

        # time-history recording
        self.recording = False
        self.max_history = 20000
        self.history: List[Dict[str, float]] = []

    # ------------------------------------------------------------------- logging
    def log_text(self) -> str:
        return "\n".join(self._log[-200:])

    def _append(self, text: str):
        self._log.append(str(text))
        print(f"[omni.fmi] {text}")

    # ------------------------------------------------------------------ lifecycle
    def attach(self, stage=None) -> int:
        stage = stage or _get_stage()
        self.detach()
        for inst in parse_stage(stage, self.root):
            if inst.kind != "fmu":
                self._append(f"Skipping {inst.prim_path}: SSP not yet supported in this build.")
                continue
            if not inst.enabled:
                continue
            if not inst.asset_path:
                self._append(f"Skipping {inst.prim_path}: no resolvable fmi:fmu asset.")
                continue
            try:
                runtime = FmuRuntime(inst.asset_path, instance_name=_instance_name(inst.prim_path))
            except Exception as exc:
                self._append(f"Failed to load {inst.asset_path}: {exc}")
                continue
            self._runtimes.append((inst, runtime))
            self._append(f"Attached {inst.prim_path} -> {inst.asset_path} (FMI {runtime.version}).")
            self._log_variables(runtime)
        self._time = 0.0
        self._outputs.clear()
        return len(self._runtimes)

    def detach(self):
        for _, runtime in self._runtimes:
            try:
                runtime.terminate()
            except Exception:
                pass
        self._runtimes.clear()
        self._outputs.clear()
        self._output_meta.clear()
        self._time = 0.0

    def reset(self):
        for _, runtime in self._runtimes:
            try:
                runtime.reset()
            except Exception:
                pass
        self._time = 0.0
        self._outputs.clear()
        self.history.clear()   # time restarts, so old samples would be misleading

    @property
    def time(self) -> float:
        return self._time

    @property
    def instance_count(self) -> int:
        return len(self._runtimes)

    # ------------------------------------------------------------- introspection
    def _log_variables(self, runtime):
        """Log the FMU's variable names by causality (what you can map in the schema)."""
        for causality in ("input", "output", "parameter"):
            names = runtime.variables(causality)
            if not names:
                continue
            preview = ", ".join(names[:12])
            more = f" (+{len(names) - 12} more)" if len(names) > 12 else ""
            self._append(f"  {causality}s: {preview}{more}")

    def describe_variables(self) -> Dict[str, Dict[str, List[str]]]:
        """{prim_path: {causality: [names]}} for every attached FMU.

        Use this to discover the exact `fmi:fmuAttribute` names to author in the
        USD-FMI schema -- especially for a tool-exported FMU (e.g. Flownex):

            from omni.fmi import FmiUsdHost
            host = FmiUsdHost(); host.attach()
            for prim, groups in host.describe_variables().items():
                print(prim)
                for causality, names in groups.items():
                    print(" ", causality, names)
        """
        described: Dict[str, Dict[str, List[str]]] = {}
        for inst, runtime in self._runtimes:
            described[inst.prim_path] = {
                causality: runtime.variables(causality)
                for causality in ("input", "output", "parameter", "local")
            }
        return described

    # ------------------------------------------------------------------- stepping
    def step(self, dt: Optional[float] = None, stage=None) -> float:
        dt = self.dt if dt is None else float(dt)
        stage = stage or _get_stage()
        sample: Dict[str, float] = {}

        for inst, runtime in self._runtimes:
            # 1) USD inputs -> FMU
            for conn in inst.connections:
                if not conn.enabled:
                    continue
                for m in conn.mappings:
                    if m.direction != "input":
                        continue
                    value = self._read_usd(stage, conn.targets, m)
                    if value is not None:
                        runtime.set_input(m.fmu_attribute, value)

            # 2) advance
            runtime.do_step(dt)

            # 3) FMU outputs -> USD (+ cache for coloring / table / history)
            for conn in inst.connections:
                if not conn.enabled:
                    continue
                for m in conn.mappings:
                    if m.direction != "output":
                        continue
                    value = runtime.get_output(m.fmu_attribute)
                    if value is None:
                        continue
                    if self.recording and isinstance(value, (int, float)):
                        sample[m.fmu_attribute] = float(value)
                    for prim_path in conn.targets:
                        self._outputs[(prim_path, m.usd_attribute)] = value
                        self._output_meta[(prim_path, m.usd_attribute)] = m.fmu_attribute
                        self._write_usd(stage, prim_path, m, value)

        self._time += dt

        if self.recording and len(sample) > 1:
            sample["time"] = self._time
            self.history.append(sample)
            if len(self.history) > self.max_history:
                del self.history[: len(self.history) - self.max_history]

        return self._time

    # --------------------------------------------------------------- inputs / outputs
    def input_bindings(self, stage=None) -> List[Dict[str, object]]:
        """Every schema-mapped FMU input, with the USD attribute driving it.

        [{fmu_attribute, prim_path, usd_attribute, value, instance}] -- the UI edits
        `value` by writing the USD attribute, so the stage stays the single source
        of truth and the next step() picks it up.
        """
        stage = stage or _get_stage()
        bindings: List[Dict[str, object]] = []
        seen = set()
        for inst, _runtime in self._runtimes:
            for conn in inst.connections:
                if not conn.enabled:
                    continue
                for m in conn.mappings:
                    if m.direction != "input" or not conn.targets:
                        continue
                    prim_path = conn.targets[0]
                    key = (prim_path, m.usd_attribute)
                    if key in seen:
                        continue
                    seen.add(key)
                    value = None
                    prim = stage.GetPrimAtPath(prim_path)
                    if prim and prim.IsValid():
                        attr = prim.GetAttribute(m.usd_attribute)
                        if attr:
                            value = attr.Get()
                    bindings.append({
                        "fmu_attribute": m.fmu_attribute,
                        "prim_path": prim_path,
                        "usd_attribute": m.usd_attribute,
                        "value": value,
                        "instance": inst.prim_path,
                    })
        return bindings

    def set_usd_value(self, prim_path: str, usd_attribute: str, value: float, stage=None) -> bool:
        """Write a USD attribute (used by the UI to drive a mapped FMU input)."""
        stage = stage or _get_stage()
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            return False
        attr = prim.GetAttribute(usd_attribute)
        if not attr:
            return False
        attr.Set(float(value))
        return True

    def output_values(self) -> List[Dict[str, object]]:
        """Latest outputs for a results table:
        [{fmu_attribute, prim_path, usd_attribute, value}] sorted by FMU variable."""
        rows = []
        for (prim_path, usd_attr), value in self._outputs.items():
            rows.append({
                "fmu_attribute": self._output_meta.get((prim_path, usd_attr), usd_attr),
                "prim_path": prim_path,
                "usd_attribute": usd_attr,
                "value": value,
            })
        rows.sort(key=lambda r: (str(r["fmu_attribute"]), str(r["prim_path"])))
        return rows

    # --------------------------------------------------------------------- history
    def clear_history(self) -> None:
        self.history.clear()

    def history_keys(self) -> List[str]:
        """Recorded variable names (excluding 'time')."""
        keys = set()
        for row in self.history:
            keys.update(row.keys())
        keys.discard("time")
        return sorted(keys)

    def history_series(self, key: str, max_points: int = 500):
        """(times, values) for one recorded variable, decimated to max_points."""
        rows = [r for r in self.history if key in r]
        if not rows:
            return [], []
        stride = max(1, len(rows) // max_points)
        rows = rows[::stride]
        return [r.get("time", 0.0) for r in rows], [r[key] for r in rows]

    def export_csv(self, path: str) -> int:
        """Write the recorded history to CSV. Returns the number of rows written."""
        import csv as _csv

        if not self.history:
            return 0
        keys = ["time"] + self.history_keys()
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = _csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            for row in self.history:
                writer.writerow(row)
        return len(self.history)

    # ------------------------------------------------------------------- coloring hooks
    def output_attributes(self) -> List[str]:
        return sorted({attr for (_, attr) in self._outputs.keys()})

    def values_for_attribute(self, usd_attribute: str) -> Dict[str, float]:
        """{prim_path: scalar} for a given output attribute -> feed straight to fastcolor."""
        return {
            prim_path: value
            for (prim_path, attr), value in self._outputs.items()
            if attr == usd_attribute and isinstance(value, (int, float))
        }

    # ------------------------------------------------------------------- USD I/O
    @staticmethod
    def _read_usd(stage, targets, m: FmuMapping) -> Optional[float]:
        if not targets:
            return None
        prim = stage.GetPrimAtPath(targets[0])
        if not prim or not prim.IsValid():
            return None
        attr = prim.GetAttribute(m.usd_attribute)
        if not attr:
            return None
        value = attr.Get()
        if value is None:
            return None
        if m.count == 0:
            return float(value) if isinstance(value, (int, float)) else None
        try:
            return float(value[m.offset])
        except Exception:
            return None

    @staticmethod
    def _write_usd(stage, prim_path, m: FmuMapping, value: float):
        from pxr import Sdf
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            return
        attr = prim.GetAttribute(m.usd_attribute)
        if m.count == 0:
            # scalar: author a Double if the attribute doesn't exist yet
            if not attr:
                attr = prim.CreateAttribute(m.usd_attribute, Sdf.ValueTypeNames.Double, custom=True)
            attr.Set(float(value))
            return
        if not attr:
            return
        current = attr.Get()
        if current is None:
            return
        try:
            current[m.offset] = float(value)   # Gf.Vec* supports item assignment
            attr.Set(current)
        except Exception:
            seq = list(current)
            seq[m.offset] = float(value)
            attr.Set(seq)
