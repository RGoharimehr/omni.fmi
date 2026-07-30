# Changelog

## [0.1.0] - 2026-07-30
### Added
- Initial scaffold of `omni.fmi` — FMI/FMU co-simulation on OpenUSD inside Kit.
- `schema.py` — parse ovfmi USD-FMI schema (FmuInstance/SspInstance/FmuConnection/FmuMapping)
  from a `pxr.Usd.Stage`.
- `fmu_runtime.py` — FMPy wrapper for FMI 2.0 / 3.0 co-simulation FMUs.
- `host.py` — `FmiUsdHost`: attach, step, route USD<->FMI, cache outputs.
- `coloring.py` — `FmiColoring`: bridge FMU outputs to `omni.fastcolor` (ScalarColorizer + LegendWindow).
- `extension.py` — Kit UI: load stage, play/pause/step, color-by variable, colormap, legend orientation.
- `example/bouncing_ball_demo.usda` — reference-FMU demo stage.

### Notes
- Design-complete scaffold; not yet validated in a Kit runtime (needs fmpy + RTX Kit + real FMUs).
- SSP, USD->FMU input routing, unit handling, and real-time pacing are on the roadmap.
