# SPDX-FileCopyrightText: 2026 Reza Goharimehr <rgoharim@villanova.edu>
# SPDX-License-Identifier: Apache-2.0
"""Thin FMPy wrapper for co-simulation FMUs (FMI 2.0 and 3.0).

Loads a self-contained CS FMU, instantiates it, and exposes set_input / get_output /
do_step. `fmpy` is imported lazily so the extension still loads (with a clear error)
if it is not yet installed. FMI variable types are read from modelDescription.xml.
"""
from typing import Any, Dict, List, Optional


class FmuRuntime:
    def __init__(self, fmu_path: str, instance_name: str = "fmu", start_time: float = 0.0):
        try:
            import fmpy
            from fmpy import read_model_description, extract
        except ImportError as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError(
                "The 'fmpy' package is required to step FMUs. Install it (declared in "
                "extension.toml [python.pipapi]) or `pip install fmpy`."
            ) from exc

        self.path = fmu_path
        self.instance_name = instance_name
        self._start = float(start_time)
        self._time = float(start_time)

        self.md = read_model_description(fmu_path)
        self.version = str(self.md.fmiVersion)
        self._fmi2 = self.version.startswith("2")
        self._unzip = extract(fmu_path)
        self._vars = {v.name: v for v in self.md.modelVariables}
        self._slave = None
        self._create()

    # ------------------------------------------------------------------ lifecycle
    def _create(self):
        model_id = self.md.coSimulation.modelIdentifier
        if self._fmi2:
            from fmpy.fmi2 import FMU2Slave
            self._slave = FMU2Slave(guid=self.md.guid, unzipDirectory=self._unzip,
                                    modelIdentifier=model_id, instanceName=self.instance_name)
            self._slave.instantiate()
            self._slave.setupExperiment(startTime=self._start)
            self._slave.enterInitializationMode()
            self._slave.exitInitializationMode()
        else:
            from fmpy.fmi3 import FMU3Slave
            self._slave = FMU3Slave(guid=self.md.guid, unzipDirectory=self._unzip,
                                    modelIdentifier=model_id, instanceName=self.instance_name)
            self._slave.instantiate()
            self._slave.enterInitializationMode(startTime=self._start)
            self._slave.exitInitializationMode()

    def reset(self):
        self.terminate()
        self._time = self._start
        self._create()

    def terminate(self):
        if self._slave is not None:
            for call in ("terminate", "freeInstance"):
                try:
                    getattr(self._slave, call)()
                except Exception:
                    pass
            self._slave = None

    # --------------------------------------------------------------------- typing
    def _kind(self, var) -> str:
        t = str(getattr(var, "type", "") or "Real")
        if t in ("Real", "Float64", "Float32"):
            return "float"
        if t in ("Integer", "Int32", "Int64", "Enumeration"):
            return "int"
        if t == "Boolean":
            return "bool"
        return "float"

    # ----------------------------------------------------------------------- I/O
    def set_input(self, name: str, value: Any):
        var = self._vars.get(name)
        if var is None or self._slave is None:
            return
        vr = [var.valueReference]
        kind = self._kind(var)
        s = self._slave
        if self._fmi2:
            if kind == "float":
                s.setReal(vr, [float(value)])
            elif kind == "int":
                s.setInteger(vr, [int(value)])
            else:
                s.setBoolean(vr, [bool(value)])
        else:
            if kind == "float":
                s.setFloat64(vr, [float(value)])
            elif kind == "int":
                s.setInt32(vr, [int(value)])
            else:
                s.setBoolean(vr, [bool(value)])

    def get_output(self, name: str) -> Optional[Any]:
        var = self._vars.get(name)
        if var is None or self._slave is None:
            return None
        vr = [var.valueReference]
        kind = self._kind(var)
        s = self._slave
        try:
            if self._fmi2:
                if kind == "float":
                    return float(s.getReal(vr)[0])
                if kind == "int":
                    return int(s.getInteger(vr)[0])
                return bool(s.getBoolean(vr)[0])
            else:
                if kind == "float":
                    return float(s.getFloat64(vr)[0])
                if kind == "int":
                    return int(s.getInt32(vr)[0])
                return bool(s.getBoolean(vr)[0])
        except Exception:
            return None

    def do_step(self, dt: float) -> float:
        if self._slave is None:
            return self._time
        try:
            if self._fmi2:
                self._slave.doStep(currentCommunicationPoint=self._time,
                                   communicationStepSize=float(dt))
            else:
                self._slave.doStep(currentCommunicationPoint=self._time,
                                   communicationStepSize=float(dt),
                                   noSetFMUStatePriorToCurrentPoint=True)
        finally:
            self._time += float(dt)
        return self._time

    # --------------------------------------------------------------------- introspection
    @property
    def time(self) -> float:
        return self._time

    def variables(self, causality: Optional[str] = None) -> List[str]:
        """Variable names, optionally filtered by causality (e.g. 'input'/'output')."""
        names = []
        for name, var in self._vars.items():
            if causality is None or str(getattr(var, "causality", "")) == causality:
                names.append(name)
        return names
