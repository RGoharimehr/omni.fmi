# SPDX-FileCopyrightText: 2026 Reza Goharimehr <rgoharim@villanova.edu>
# SPDX-License-Identifier: Apache-2.0
"""Validate PipeHeatLoad.fmu with FMPy (no Kit needed).

    python fmu/pipe_heat_load/validate.py
"""
import os
import sys

from fmpy import read_model_description, extract
from fmpy.fmi2 import FMU2Slave

HERE = os.path.dirname(os.path.abspath(__file__))
FMU = os.path.normpath(os.path.join(HERE, "..", "..", "example", "PipeHeatLoad.fmu"))


def main():
    md = read_model_description(FMU)
    print(f"model: {md.modelName}  FMI {md.fmiVersion}")
    by_causality = {}
    vr = {}
    for v in md.modelVariables:
        by_causality.setdefault(str(v.causality), []).append(v.name)
        vr[v.name] = v.valueReference
    for causality, names in by_causality.items():
        print(f"  {causality}: {', '.join(names)}")

    fmu = FMU2Slave(guid=md.guid, unzipDirectory=extract(FMU),
                    modelIdentifier=md.coSimulation.modelIdentifier, instanceName="pipe")
    fmu.instantiate()
    fmu.setupExperiment(startTime=0.0)
    fmu.enterInitializationMode()
    fmu.exitInitializationMode()

    seg_T = [vr[f"T_{i}"] for i in range(1, 9)]
    seg_x = [vr[f"x_{i}"] for i in range(1, 9)]
    out = [vr["T_out"], vr["x_out"], vr["dp_total"], vr["Q_absorbed"]]

    def report(tag):
        T = fmu.getReal(seg_T)
        x = fmu.getReal(seg_x)
        T_out, x_out, dp, q = fmu.getReal(out)
        print(f"\n{tag}")
        print("  T[degC] " + " ".join(f"{v:6.1f}" for v in T))
        print("  x[-]    " + " ".join(f"{v:6.3f}" for v in x))
        print(f"  T_out={T_out:.1f} C  x_out={x_out:.3f}  dp={dp:.2f} kPa  Q_abs={q:.0f} W")
        return T, x

    dt = 0.05

    # --- case 1: moderate load, should stay liquid ---------------------------
    fmu.setReal([vr["Q_total"]], [3000.0])
    for _ in range(400):
        fmu.doStep(currentCommunicationPoint=fmu.time if hasattr(fmu, "time") else 0.0,
                   communicationStepSize=dt)
    T1, x1 = report("case 1: Q=3000 W, m_dot=0.05 (expect heating, no boiling)")

    # --- case 2: high load, should boil ------------------------------------
    fmu.setReal([vr["Q_total"]], [30000.0])
    for _ in range(400):
        fmu.doStep(currentCommunicationPoint=0.0, communicationStepSize=dt)
    T2, x2 = report("case 2: Q=30000 W (expect T pinned at T_sat=60 and quality rising)")

    # --- case 3: raise saturation temperature (tunable) ---------------------
    fmu.setReal([vr["T_sat"]], [90.0])
    for _ in range(400):
        fmu.doStep(currentCommunicationPoint=0.0, communicationStepSize=dt)
    T3, x3 = report("case 3: T_sat raised to 90 C (boiling front should move downstream)")

    fmu.terminate()
    fmu.freeInstance()

    # --- assertions ---------------------------------------------------------
    ok = True
    if not all(T1[i] <= T1[i + 1] + 1e-6 for i in range(7)):
        print("FAIL: temperature should rise monotonically along the pipe"); ok = False
    if max(x1) > 1e-6:
        print("FAIL: case 1 should not boil"); ok = False
    if max(x2) <= 0.0:
        print("FAIL: case 2 should boil"); ok = False
    if max(T2) > 60.5:
        print("FAIL: case 2 temperature should be capped at T_sat"); ok = False
    if max(T3) <= 60.5:
        print("FAIL: case 3 should heat past the old T_sat"); ok = False
    if not all(0.0 <= v <= 1.0 for v in x1 + x2 + x3):
        print("FAIL: quality must stay within [0, 1]"); ok = False

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
