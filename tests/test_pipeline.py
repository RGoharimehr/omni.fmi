# SPDX-FileCopyrightText: 2026 Reza Goharimehr <rgoharim@villanova.edu>
# SPDX-License-Identifier: Apache-2.0
"""End-to-end test of the omni.fmi pipeline WITHOUT Kit.

Exercises the real `FmiUsdHost` against the PipeHeatLoad demo stage:
schema parse -> FMU load -> USD inputs -> step -> USD outputs.

`host.py` has no module-level `omni` imports and both `attach()`/`step()` accept
an explicit stage, so the whole data path runs on plain `pxr` + `fmpy`.

    pip install usd-core fmpy
    python tests/test_pipeline.py
"""
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
STAGE = os.path.join(ROOT, "example", "pipe_heat_load_demo.usda")
FMU = os.path.join(ROOT, "example", "PipeHeatLoad.fmu")


def _load_host_module():
    """Import omni/fmi/{schema,fmu_runtime,host}.py as a package, skipping
    __init__.py (which pulls in omni.ext/omni.ui and needs a Kit runtime)."""
    pkg = types.ModuleType("_fmipkg")
    pkg.__path__ = [os.path.join(ROOT, "omni", "fmi")]
    sys.modules["_fmipkg"] = pkg
    for name in ("schema", "fmu_runtime", "host"):
        path = os.path.join(ROOT, "omni", "fmi", f"{name}.py")
        spec = importlib.util.spec_from_file_location(f"_fmipkg.{name}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"_fmipkg.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["_fmipkg.host"]


def main():
    if not os.path.exists(FMU):
        print(f"SKIP: {FMU} not built. Run fmu/pipe_heat_load/build.ps1 first.")
        return 0

    from pxr import Usd

    host_mod = _load_host_module()
    stage = Usd.Stage.Open(STAGE)
    host = host_mod.FmiUsdHost(root="/World", dt=0.05)

    n = host.attach(stage=stage)
    print(f"attached instances: {n}")
    assert n == 1, "expected exactly one FmuInstance"

    described = host.describe_variables()
    for prim, groups in described.items():
        print(f"  {prim}: inputs={groups['input']}")
        print(f"    outputs={len(groups['output'])} parameters={groups['parameter']}")

    controls = stage.GetPrimAtPath("/World/Controls")
    seg_paths = [f"/World/Pipe/Seg_{i:02d}" for i in range(1, 9)]

    def temps():
        return [stage.GetPrimAtPath(p).GetAttribute("sim:value").Get() for p in seg_paths]

    def qualities():
        return [stage.GetPrimAtPath(p).GetAttribute("sim:quality").Get() for p in seg_paths]

    def run(seconds=20.0):
        for _ in range(int(seconds / 0.05)):
            host.step(0.05, stage=stage)

    ok = True

    # --- case 1: default load, liquid only -----------------------------------
    controls.GetAttribute("sim:heat_load").Set(3000.0)
    run()
    T, x = temps(), qualities()
    print("\ncase 1  Q=3000 W")
    print("  T " + " ".join(f"{v:6.1f}" for v in T))
    print("  x " + " ".join(f"{v:6.3f}" for v in x))
    if not all(T[i] <= T[i + 1] + 1e-6 for i in range(7)):
        print("  FAIL: temperature should rise along the pipe"); ok = False
    if max(x) > 1e-6:
        print("  FAIL: should not boil at 3000 W"); ok = False

    # --- case 2: high load via USD input -> boiling ---------------------------
    controls.GetAttribute("sim:heat_load").Set(30000.0)
    run()
    T, x = temps(), qualities()
    print("\ncase 2  Q=30000 W (USD -> FMU input)")
    print("  T " + " ".join(f"{v:6.1f}" for v in T))
    print("  x " + " ".join(f"{v:6.3f}" for v in x))
    if max(x) <= 0.0:
        print("  FAIL: should boil at 30000 W"); ok = False
    if max(T) > 60.5:
        print("  FAIL: temperature should cap at T_sat=60"); ok = False

    # --- case 3: raise T_sat -> boiling front moves downstream ----------------
    first_boiling_before = next((i for i, v in enumerate(x) if v > 1e-6), 99)
    controls.GetAttribute("sim:sat_temp").Set(90.0)
    run()
    T, x = temps(), qualities()
    first_boiling_after = next((i for i, v in enumerate(x) if v > 1e-6), 99)
    print("\ncase 3  T_sat=90 C")
    print("  T " + " ".join(f"{v:6.1f}" for v in T))
    print("  x " + " ".join(f"{v:6.3f}" for v in x))
    if first_boiling_after <= first_boiling_before:
        print("  FAIL: boiling front should move downstream"); ok = False
    if max(T) <= 60.5:
        print("  FAIL: should now heat past the old T_sat"); ok = False

    # --- coloring hook --------------------------------------------------------
    values = host.values_for_attribute("sim:value")
    print(f"\nvalues_for_attribute('sim:value'): {len(values)} prims")
    if len(values) != 8:
        print("  FAIL: expected 8 prim values for coloring"); ok = False
    if sorted(values.keys()) != sorted(seg_paths):
        print("  FAIL: coloring prim paths do not match the pipe segments"); ok = False
    print(f"  output attributes: {host.output_attributes()}")

    host.detach()
    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
