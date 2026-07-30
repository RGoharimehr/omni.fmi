# SPDX-FileCopyrightText: 2026 Reza Goharimehr <rgoharim@villanova.edu>
# SPDX-License-Identifier: Apache-2.0
"""omni.fmi - FMI/FMU co-simulation on OpenUSD, for Omniverse Kit.

Parses the ovfmi USD-FMI schema on the omni.usd stage, steps FMUs with FMPy, and
colors results with omni.fastcolor. ovfmi is the design reference; this extension
runs inside Kit (not ovstage).

Developer: Reza Goharimehr <rgoharim@villanova.edu> (Villanova University)
"""
from . import schema
from .host import FmiUsdHost
from .extension import FmiUsdExtension

__version__ = "0.1.0"
__author__ = "Reza Goharimehr"
__email__ = "rgoharim@villanova.edu"

__all__ = ["FmiUsdExtension", "FmiUsdHost", "schema", "__version__", "__author__", "__email__"]
