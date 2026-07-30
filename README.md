# omni.fmi

**FMI/FMU co-simulation on OpenUSD, inside NVIDIA Omniverse Kit — colored by
[omni.fastcolor](https://github.com/RGoharimehr/omni.fastcolor).**

`omni.fmi` loads FMI co-simulation models that are declared **declaratively on a USD stage**
(NVIDIA ovfmi's USD-FMI schema), steps them with **FMPy**, routes values between USD attributes and
FMU variables, and visualizes outputs with `omni.fastcolor`. It is the standards-based path toward
engine-agnostic digital twins: Flownex (or any tool) exports a self-contained FMU, and this
extension drives it on the stage — no vendor COM/API at runtime.

- **Developer:** Reza Goharimehr — <rgoharim@villanova.edu> (Villanova University)
- **Status:** v0.1.0 scaffold. Design-complete; **not yet run inside Kit** — needs `fmpy`, an RTX
  Kit runtime, and real FMUs to validate. Pure-Python parts are syntax-checked.

---

## Why not just use ovfmi?

NVIDIA `ovfmi` is the **design reference** for this project, but its `FmiHost` is bound to the
**ovstage/ovrtx** runtime (a standalone app), not to Kit's `omni.usd`. Since we want to reuse
`omni.fastcolor` (a Kit extension) and live in the Kit ecosystem, `omni.fmi` keeps ovfmi's
**declarative USD-FMI schema** and API shape, but drives a `pxr.Usd.Stage` via `omni.usd` and steps
FMUs with **FMPy** directly. ovfmi's ovstage renderer remains available if you later want the
standalone high-fidelity path.

---

## Architecture

```
USD stage (omni.usd)                         FMPy                      omni.fastcolor
FmuInstance / FmuConnection / FmuMapping  →  load .fmu, do_step   →   outputs → displayColor + legend
      schema.py (parse)                       fmu_runtime.py            coloring.py
                     host.py: read USD inputs → step → write/cache outputs
                          extension.py: Kit UI (load, play/step/dt, color-by, colormap, legend)
```

| Module | Role |
|--------|------|
| `schema.py`      | Parse `FmuInstance`/`SspInstance`/`FmuConnection`/`FmuMapping` from a `pxr.Usd.Stage`. |
| `fmu_runtime.py` | FMPy wrapper for FMI 2.0 / 3.0 co-simulation FMUs (`set_input`/`get_output`/`do_step`). |
| `host.py`        | `FmiUsdHost` — attach, step, route USD↔FMI, cache outputs for coloring. |
| `coloring.py`    | `FmiColoring` — feed cached outputs to `omni.fastcolor` (`ScalarColorizer` + `LegendWindow`). |
| `extension.py`   | Kit UI (`omni.ext.IExt`): load stage, play/pause/step, choose color variable, colormap, legend. |

---

## USD-FMI schema (authored on the stage)

```usda
def FmuInstance "BallFmu"
{
    bool  fmi:enabled = 1
    asset fmi:fmu = @./BouncingBall.fmu@
    def FmuConnection "BallConn"
    {
        rel fmi:targets = </World/Ball>
        def FmuMapping "Height" {
            token fmi:direction    = "output"          # "input" USD->FMU | "output" FMU->USD
            token fmi:fmuAttribute = "h"               # variable in modelDescription.xml
            token fmi:usdAttribute = "xformOp:translate"
            int2  fmi:usdMapping   = (1, 1)            # (offset, count); (0,0)=scalar, (1,1)=Y
        }
        def FmuMapping "Speed" {
            token fmi:direction    = "output"
            token fmi:fmuAttribute = "v"
            token fmi:usdAttribute = "sim:value"       # a scalar to color by
            int2  fmi:usdMapping   = (0, 0)
        }
    }
}
```

---

## Install

1. Add the folder that contains `omni.fmi` to Kit's *Extension Search Paths* (and likewise for
   `omni.fastcolor`), or launch with `--ext-folder`.
2. Enable **FMI on USD** (`omni.fmi`). It depends on `omni.fastcolor` and pip-installs `fmpy`
   (declared in `extension.toml [python.pipapi]`).

---

## Quick start — the BouncingBall example

1. Get a `BouncingBall.fmu` (FMI 3.0 CS) from the Modelica **reference-fmus** releases
   (<https://github.com/modelica/reference-fmus/releases>) or build it, and place it next to
   [`example/bouncing_ball_demo.usda`](example/bouncing_ball_demo.usda).
2. In the **FMI on USD** window: set **Stage** to that `.usda`, click **Open**, then **Attach FMUs**.
3. Set **Color by** = `sim:value`, tick **Manual bounds** with Min `-3` / Max `3`, then **Play**.
   The sphere bounces (height `h` drives Y), and its color tracks velocity `v` via `omni.fastcolor`;
   the legend shows the range. Flip the legend to horizontal from the dropdown.

> Reference FMUs first is deliberate: if the pipeline works on a known-good FMU, any later failure is
> clearly on the Flownex-FMU side, not the plumbing.

---

## Public API

```python
from omni.fmi import FmiUsdHost, schema

host = FmiUsdHost(root="/World", dt=1/60)
host.attach()                       # parse schema on the current stage, load FMUs
host.step(dt)                       # read USD inputs -> FMU -> step -> write/cache outputs
host.values_for_attribute("sim:value")   # {prim_path: scalar} -> hand to omni.fastcolor
host.output_attributes()            # which USD attributes outputs were written to
host.detach()

schema.parse_stage(stage, "/World") # -> list[FmuInstance] (pure, no FMPy needed)
```

---

## Roadmap

- **SSP** (`SspInstance`) — multi-FMU networks via `.ssp`/`.ssd` (component/connector/connection graph).
- **Flownex FMU** — swap the reference FMU for a Flownex-exported CS FMU (the real cooling case).
- Input mappings from USD → FMU (partially implemented), unit handling, and multi-component vectors.
- Real-time pacing (accumulate wall-clock instead of one step per frame).

## Related

- [omni.fastcolor](https://github.com/RGoharimehr/omni.fastcolor) — the coloring engine used here.
- NVIDIA ovfmi (`omniverse-labs/projects/ovfmi`) — the design reference.
- Modelica reference-fmus / FMI 3.0 — the standard and its test models.

## License

Apache-2.0 — see [`LICENSE`](LICENSE).

## Contact

Reza Goharimehr — <rgoharim@villanova.edu>
