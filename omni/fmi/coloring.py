# SPDX-FileCopyrightText: 2026 Reza Goharimehr <rgoharim@villanova.edu>
# SPDX-License-Identifier: Apache-2.0
"""Bridge FMU outputs to omni.fastcolor.

Given the host's cached outputs and a chosen output attribute, build the
{prim_path: scalar} dict and hand it to fastcolor's ScalarColorizer, plus keep a
LegendWindow in sync. All the coloring speed/caching lives in omni.fastcolor.
"""
from typing import Optional

from omni.fastcolor import ScalarColorizer, LegendWindow


class FmiColoring:
    def __init__(self, colormap: str = "viridis", orientation: str = "vertical"):
        self._colorizer = ScalarColorizer(colormap)
        self._legend = LegendWindow(orientation=orientation)
        self._attribute: Optional[str] = None

    # ---- config ----
    def set_attribute(self, usd_attribute: Optional[str]):
        self._attribute = usd_attribute

    def set_colormap(self, name: str):
        self._colorizer.set_colormap(name)

    def set_orientation(self, orientation: str):
        self._legend.set_orientation(orientation)

    def set_font_sizes(self, title=None, tick=None):
        self._legend.set_font_sizes(title=title, tick=tick)

    @property
    def attribute(self) -> Optional[str]:
        return self._attribute

    # ---- per-step update ----
    def update(self, host, vmin: Optional[float] = None, vmax: Optional[float] = None,
               label: Optional[str] = None) -> dict:
        if not self._attribute:
            return {"colored": 0}
        values = host.values_for_attribute(self._attribute)
        result = self._colorizer.colorize(values, vmin, vmax)
        self._legend.update(result.get("vmin"), result.get("vmax"),
                            self._colorizer.colormap, label or self._attribute)
        return result

    def clear(self):
        self._colorizer.clear()
        self._legend.clear()

    def destroy(self):
        try:
            self._colorizer.clear()
        except Exception:
            pass
        self._legend.destroy()
