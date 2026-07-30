# SPDX-FileCopyrightText: 2026 Reza Goharimehr <rgoharim@villanova.edu>
# SPDX-License-Identifier: Apache-2.0
"""Generate example/pipe_heat_load_demo.usda for the PipeHeatLoad FMU.

    python fmu/pipe_heat_load/make_demo_stage.py [n_segments]

Keeps the stage and the FMU's segment count in sync (the FMU is built with
N_SEG = 8; regenerate here if you change it in PipeHeatLoad.c).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "..", "example", "pipe_heat_load_demo.usda"))

SEG_LEN = 0.5
SEG_GAP = 0.02
RADIUS = 0.07

HEADER = '''#usda 1.0
(
    doc = "omni.fmi demo: a heated pipe (PipeHeatLoad.fmu) split into {n} segments. Each segment prim receives its own temperature (sim:value) and vapour quality (sim:quality) from the FMU. Attach FMUs, press Play, then colour by sim:value for the thermal gradient or sim:quality for the boiling front. Drive it live from /World/Controls."
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Y"
)

def Xform "World"
{{
    # Edit these while playing to drive the simulation.
    def Xform "Controls"
    {{
        custom double sim:heat_load = 5000.0
        custom double sim:mass_flow = 0.05
        custom double sim:inlet_temp = 25.0
        custom double sim:sat_temp = 60.0
        double3 xformOp:translate = (0, 1.5, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }}

    def FmuInstance "PipeFmu"
    {{
        bool fmi:enabled = 1
        asset fmi:fmu = @./PipeHeatLoad.fmu@

        def FmuConnection "Inputs"
        {{
            rel fmi:targets = </World/Controls>
'''

INPUT_MAPPINGS = [
    ("HeatLoad", "Q_total", "sim:heat_load"),
    ("MassFlow", "m_dot", "sim:mass_flow"),
    ("InletTemp", "T_in", "sim:inlet_temp"),
    ("SatTemp", "T_sat", "sim:sat_temp"),
]


def mapping_block(name, direction, fmu_attr, usd_attr, indent):
    pad = " " * indent
    return (
        f'{pad}def FmuMapping "{name}"\n'
        f'{pad}{{\n'
        f'{pad}    token fmi:direction = "{direction}"\n'
        f'{pad}    token fmi:fmuAttribute = "{fmu_attr}"\n'
        f'{pad}    token fmi:usdAttribute = "{usd_attr}"\n'
        f'{pad}    int2 fmi:usdMapping = (0, 0)\n'
        f'{pad}}}\n'
    )


def build(n=8):
    parts = [HEADER.format(n=n)]

    for name, fmu_attr, usd_attr in INPUT_MAPPINGS:
        parts.append(mapping_block(name, "input", fmu_attr, usd_attr, 12))
    parts.append("        }\n")

    # one connection per segment: temperature + vapour quality
    for i in range(1, n + 1):
        parts.append(f'\n        def FmuConnection "Seg{i:02d}"\n        {{\n')
        parts.append(f'            rel fmi:targets = </World/Pipe/Seg_{i:02d}>\n')
        parts.append(mapping_block("Temp", "output", f"T_{i}", "sim:value", 12))
        parts.append(mapping_block("Quality", "output", f"x_{i}", "sim:quality", 12))
        parts.append("        }\n")
    parts.append("    }\n\n")

    # the pipe geometry
    parts.append('    def Xform "Pipe"\n    {\n')
    for i in range(1, n + 1):
        x = (i - 1) * (SEG_LEN + SEG_GAP)
        parts.append(
            f'        def Cylinder "Seg_{i:02d}"\n'
            f'        {{\n'
            f'            uniform token axis = "X"\n'
            f'            double height = {SEG_LEN}\n'
            f'            double radius = {RADIUS}\n'
            f'            custom double sim:value = 25.0\n'
            f'            custom double sim:quality = 0.0\n'
            f'            double3 xformOp:translate = ({x:.2f}, 0, 0)\n'
            f'            uniform token[] xformOpOrder = ["xformOp:translate"]\n'
            f'        }}\n'
        )
    parts.append("    }\n}\n")
    return "".join(parts)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(build(n))
    print(f"wrote {OUT} ({n} segments)")
