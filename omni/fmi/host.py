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
        self._time = 0.0
        self._log: List[str] = []

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
        self._time = 0.0

    def reset(self):
        for _, runtime in self._runtimes:
            try:
                runtime.reset()
            except Exception:
                pass
        self._time = 0.0
        self._outputs.clear()

    @property
    def time(self) -> float:
        return self._time

    @property
    def instance_count(self) -> int:
        return len(self._runtimes)

    # ------------------------------------------------------------------- stepping
    def step(self, dt: Optional[float] = None, stage=None) -> float:
        dt = self.dt if dt is None else float(dt)
        stage = stage or _get_stage()

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

            # 3) FMU outputs -> USD (+ cache for coloring)
            for conn in inst.connections:
                if not conn.enabled:
                    continue
                for m in conn.mappings:
                    if m.direction != "output":
                        continue
                    value = runtime.get_output(m.fmu_attribute)
                    if value is None:
                        continue
                    for prim_path in conn.targets:
                        self._outputs[(prim_path, m.usd_attribute)] = value
                        self._write_usd(stage, prim_path, m, value)

        self._time += dt
        return self._time

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
