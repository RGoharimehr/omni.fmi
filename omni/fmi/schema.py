# SPDX-FileCopyrightText: 2026 Reza Goharimehr <rgoharim@villanova.edu>
# SPDX-License-Identifier: Apache-2.0
"""Parse the ovfmi USD-FMI schema from a USD stage into plain Python structures.

Mirrors NVIDIA ovfmi's declarative schema (docs/USD-FMI-SCHEMA.md) but reads a
`pxr.Usd.Stage` directly, so it works inside Kit (omni.usd) rather than ovstage:

    def FmuInstance "Ctrl" {                 # or SspInstance (asset fmi:ssp)
        asset fmi:fmu = @./Model.fmu@
        bool  fmi:enabled = 1
        def FmuConnection "C" {
            rel fmi:targets = </World/Cube>
            def FmuMapping "M" {
                token fmi:direction   = "output"    # "input" USD->FMU | "output" FMU->USD
                token fmi:fmuAttribute = "x"        # variable in modelDescription.xml
                token fmi:usdAttribute = "sim:value"
                int2  fmi:usdMapping   = (0, 0)     # (offset, count); (0,0)=scalar
            }
        }
    }
"""
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class FmuMapping:
    direction: str            # "input" | "output"
    fmu_attribute: str
    usd_attribute: str
    offset: int = 0
    count: int = 0            # 0 => scalar (whole attribute)


@dataclass
class FmuConnection:
    targets: List[str] = field(default_factory=list)   # USD prim paths
    mappings: List[FmuMapping] = field(default_factory=list)
    enabled: bool = True


@dataclass
class FmuInstance:
    prim_path: str
    kind: str                 # "fmu" | "ssp"
    asset_path: str           # resolved filesystem path to .fmu / .ssp
    enabled: bool = True
    connections: List[FmuConnection] = field(default_factory=list)


def _bool(attr, default=True) -> bool:
    if not attr:
        return default
    value = attr.Get()
    return default if value is None else bool(value)


def _token(attr, default="") -> str:
    if not attr:
        return default
    value = attr.Get()
    return default if value is None else str(value)


def _int2(attr) -> Tuple[int, int]:
    if not attr:
        return (0, 0)
    value = attr.Get()
    if value is None:
        return (0, 0)
    try:
        return (int(value[0]), int(value[1]))
    except Exception:
        return (0, 0)


def _resolve_asset(attr) -> str:
    if not attr:
        return ""
    asset = attr.Get()
    if asset is None:
        return ""
    # Sdf.AssetPath: prefer the resolved path, fall back to the authored path.
    return getattr(asset, "resolvedPath", "") or getattr(asset, "path", "") or str(asset)


def parse_stage(stage, root: str = "/World") -> List[FmuInstance]:
    """Return every enabled/disabled FmuInstance/SspInstance under `root`."""
    from pxr import Usd

    root_prim = stage.GetPrimAtPath(root) if root else None
    if not root_prim or not root_prim.IsValid():
        root_prim = stage.GetPseudoRoot()

    instances: List[FmuInstance] = []
    for prim in Usd.PrimRange(root_prim):
        type_name = prim.GetTypeName()
        if type_name == "FmuInstance":
            kind, asset_attr = "fmu", "fmi:fmu"
        elif type_name == "SspInstance":
            kind, asset_attr = "ssp", "fmi:ssp"
        else:
            continue

        inst = FmuInstance(
            prim_path=prim.GetPath().pathString,
            kind=kind,
            asset_path=_resolve_asset(prim.GetAttribute(asset_attr)),
            enabled=_bool(prim.GetAttribute("fmi:enabled"), True),
        )

        for child in prim.GetChildren():
            if child.GetTypeName() != "FmuConnection":
                continue
            conn = FmuConnection(enabled=_bool(child.GetAttribute("fmi:enabled"), True))
            rel = child.GetRelationship("fmi:targets")
            if rel:
                conn.targets = [t.pathString for t in rel.GetTargets()]
            for mapping_prim in child.GetChildren():
                if mapping_prim.GetTypeName() != "FmuMapping":
                    continue
                offset, count = _int2(mapping_prim.GetAttribute("fmi:usdMapping"))
                conn.mappings.append(FmuMapping(
                    direction=_token(mapping_prim.GetAttribute("fmi:direction"), "output").strip().lower(),
                    fmu_attribute=_token(mapping_prim.GetAttribute("fmi:fmuAttribute"), ""),
                    usd_attribute=_token(mapping_prim.GetAttribute("fmi:usdAttribute"), ""),
                    offset=offset,
                    count=count,
                ))
            inst.connections.append(conn)

        instances.append(inst)
    return instances
