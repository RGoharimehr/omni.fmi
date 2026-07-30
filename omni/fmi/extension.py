# SPDX-FileCopyrightText: 2026 Reza Goharimehr <rgoharim@villanova.edu>
# SPDX-License-Identifier: Apache-2.0
"""Kit UI for omni.fmi.

Sections: Setup - Simulation - Inputs - Outputs - Coloring - Recording - Log.

Performance notes: widgets are built once and their `.text` / plot data updated in
place; refreshes are throttled to ~10 Hz so stepping at frame rate does not cost
UI time. Input fields are only re-read from USD on attach/Refresh so they never
fight the user mid-edit.
"""
import os
import time

import omni.ext
import omni.ui as ui
import omni.kit.app
import omni.usd
from omni.ui import color as cl

from .host import FmiUsdHost

_UI_REFRESH_PERIOD = 0.1     # seconds between table/plot refreshes
_MAX_SUBSTEPS = 8            # cap so a hitch cannot spiral into catch-up steps
_PLOT_COLORS = ["#00E6D9", "#FF007F", "#9D6CFF", "#FFD700", "#32CD32", "#FF5733"]


class FmiUsdExtension(omni.ext.IExt):
    # ------------------------------------------------------------------ lifecycle
    def on_startup(self, ext_id: str):
        self._host = FmiUsdHost()
        self._coloring = None
        self._playing = False
        self._accum = 0.0
        self._last_wall = None
        self._last_ui_refresh = 0.0

        self._attr_options = []
        self._attr_combo = None
        self._input_rows = []          # [(binding, FloatField)]
        self._output_rows = {}         # key -> value ui.Label
        self._output_keys = []
        self._plot_keys = []
        self._plots = {}               # key -> (ui.Plot, ui.Label range)

        self._window = ui.Window("FMI on USD", width=460, height=900)
        with self._window.frame:
            self._build_ui()

        self._sub = (
            omni.kit.app.get_app()
            .get_update_event_stream()
            .create_subscription_to_pop(self._on_update, name="omni.fmi.step")
        )

    def get_host(self) -> FmiUsdHost:
        return self._host

    def on_shutdown(self):
        self._playing = False
        self._sub = None
        try:
            self._host.detach()
        except Exception:
            pass
        if self._coloring:
            self._coloring.destroy()
            self._coloring = None
        if self._window:
            self._window.destroy()
            self._window = None

    # ------------------------------------------------------------------------ UI
    def _build_ui(self):
        from omni.fastcolor import colormaps
        self._cmaps = colormaps.available()

        with ui.ScrollingFrame():
            with ui.VStack(spacing=6, height=0):
                self._build_setup()
                self._build_simulation()
                self._build_inputs()
                self._build_outputs()
                self._build_coloring()
                self._build_recording()
                self._build_log()

        # populate the (empty) plot selector so the control is visible up-front
        self._rebuild_plot_selector()

    def _section(self, title, collapsed=False):
        frame = ui.CollapsableFrame(title, collapsed=collapsed)
        return frame

    def _build_setup(self):
        with self._section("Setup"):
            with ui.VStack(spacing=5, height=0):
                with ui.HStack(height=24):
                    ui.Label("Stage:", width=54)
                    self._stage_field = ui.StringField()
                    ui.Button("Open", width=54, clicked_fn=self._on_open_stage)
                with ui.HStack(height=26, spacing=5):
                    ui.Button("Attach FMUs", clicked_fn=self._on_attach)
                    ui.Button("Detach", clicked_fn=self._on_detach)
                    ui.Button("Reset", clicked_fn=self._on_reset)
                self._status = ui.Label("Ready.", style={"font_size": 13})

    def _build_simulation(self):
        with self._section("Simulation"):
            with ui.VStack(spacing=5, height=0):
                with ui.HStack(height=24, spacing=6):
                    ui.Label("dt [s]:", width=54)
                    self._dt_field = ui.FloatField(width=70)
                    self._dt_field.model.set_value(1.0 / 60.0)
                    self._play_button = ui.Button("Play", width=64, clicked_fn=self._on_toggle_play)
                    ui.Button("Step", width=54, clicked_fn=lambda: self._do_step())
                with ui.HStack(height=22, spacing=4):
                    self._realtime = ui.CheckBox(width=18)
                    self._realtime.model.set_value(True)
                    ui.Label("Real-time pacing", width=120)
                    ui.Label("Speed", width=42)
                    self._speed_field = ui.FloatField(width=54)
                    self._speed_field.model.set_value(1.0)
                    ui.Label("x", width=12)
                self._time_label = ui.Label("t = 0.000 s", style={"font_size": 15})

    def _build_inputs(self):
        with self._section("Inputs (drive the FMU from USD)"):
            with ui.VStack(spacing=4, height=0):
                ui.Label("Values are written to the mapped USD attribute, so the stage stays "
                         "the source of truth.", style={"font_size": 11, "color": cl("#999999")},
                         word_wrap=True)
                self._inputs_container = ui.VStack(spacing=3, height=0)
                ui.Button("Refresh from USD", height=22, clicked_fn=self._refresh_input_values)

    def _build_outputs(self):
        with self._section("Outputs"):
            with ui.VStack(spacing=4, height=0):
                self._outputs_container = ui.VStack(spacing=2, height=0)

    def _build_coloring(self):
        with self._section("Coloring (omni.fastcolor)"):
            with ui.VStack(spacing=5, height=0):
                with ui.HStack(height=24):
                    ui.Label("Color by:", width=76)
                    self._attr_container = ui.HStack()
                with ui.HStack(height=24):
                    ui.Label("Colormap:", width=76)
                    self._cmap_combo = ui.ComboBox(0, *self._cmaps)
                    self._cmap_combo.model.add_item_changed_fn(self._on_cmap_changed)
                with ui.HStack(height=24, spacing=4):
                    self._manual = ui.CheckBox(width=18)
                    ui.Label("Manual bounds", width=100)
                    ui.Label("Min", width=26)
                    self._min_field = ui.StringField(width=56)
                    ui.Label("Max", width=26)
                    self._max_field = ui.StringField(width=56)
                with ui.HStack(height=24):
                    ui.Label("Legend:", width=76)
                    self._orient_combo = ui.ComboBox(0, "vertical", "horizontal")
                    self._orient_combo.model.add_item_changed_fn(self._on_orient_changed)
                with ui.HStack(height=24, spacing=4):
                    ui.Label("Label:", width=76)
                    self._label_field = ui.StringField()
                    ui.Label("Units:", width=40)
                    self._units_field = ui.StringField(width=64)
                with ui.HStack(height=24, spacing=4):
                    ui.Label("Font size:", width=76)
                    self._font_field = ui.FloatField(width=54)
                    self._font_field.model.set_value(14.0)
                    self._font_field.model.add_end_edit_fn(lambda *_: self._on_font_changed())

    def _build_recording(self):
        with self._section("Recording", collapsed=True):
            with ui.VStack(spacing=5, height=0):
                with ui.HStack(height=22, spacing=4):
                    self._record_check = ui.CheckBox(width=18)
                    self._record_check.model.add_value_changed_fn(self._on_record_toggled)
                    ui.Label("Record time history", width=140)
                    self._history_label = ui.Label("0 rows", style={"font_size": 12})
                with ui.HStack(height=24, spacing=5):
                    ui.Button("Clear", width=70, clicked_fn=self._on_clear_history)
                    ui.Button("Export CSV", width=90, clicked_fn=self._on_export_csv)
                with ui.HStack(height=24):
                    ui.Label("CSV path:", width=64)
                    self._csv_field = ui.StringField()
                    self._csv_field.model.set_value(
                        os.path.join(os.path.expanduser("~"), "fmi_history.csv"))

                ui.Separator(height=3)
                with ui.HStack(height=24, spacing=5):
                    ui.Label("Plot:", width=40)
                    self._plot_container = ui.HStack()
                    ui.Button("Add", width=48, clicked_fn=self._on_add_plot)
                    ui.Button("Clear", width=54, clicked_fn=self._on_clear_plots)
                self._plots_area = ui.VStack(spacing=6, height=0)

    def _build_log(self):
        with self._section("Log", collapsed=True):
            self._log_field = ui.StringField(multiline=True, height=130, read_only=True)

    # ------------------------------------------------------------------ handlers
    def _on_open_stage(self):
        path = self._stage_field.model.get_value_as_string().strip()
        if path:
            omni.usd.get_context().open_stage(path)
            self._set_status(f"Opened {os.path.basename(path)}")

    def _on_attach(self):
        try:
            n = self._host.attach()
            self._set_status(f"Attached {n} FMU instance(s).")
            self._rebuild_input_rows()
            self._output_keys = []
            self._rebuild_output_rows()
        except Exception as error:
            self._set_status(f"Attach failed: {error}")
        self._refresh_log()

    def _on_detach(self):
        self._playing = False
        self._play_button.text = "Play"
        self._host.detach()
        if self._coloring:
            self._coloring.clear()
        self._rebuild_input_rows()
        self._output_keys = []
        self._rebuild_output_rows()
        self._set_status("Detached.")

    def _on_reset(self):
        self._host.reset()
        self._accum = 0.0
        self._last_wall = time.perf_counter()
        self._set_status("Reset to t = 0.")
        self._refresh_dynamic(force=True)

    def _on_toggle_play(self):
        self._playing = not self._playing
        self._play_button.text = "Pause" if self._playing else "Play"
        self._last_wall = time.perf_counter()
        self._accum = 0.0

    def _on_cmap_changed(self, *_args):
        if self._coloring:
            self._coloring.set_colormap(self._selected_cmap())

    def _on_orient_changed(self, *_args):
        idx = self._orient_combo.model.get_item_value_model().as_int
        if self._coloring:
            self._coloring.set_orientation("horizontal" if idx == 1 else "vertical")

    def _on_font_changed(self):
        if self._coloring:
            size = max(6.0, self._font_field.model.get_value_as_float())
            self._coloring.set_font_sizes(title=size + 4.0, tick=size)

    def _on_record_toggled(self, model):
        self._host.recording = model.get_value_as_bool()

    def _on_clear_history(self):
        self._host.clear_history()
        self._rebuild_plot_selector()
        self._set_status("History cleared.")

    def _on_export_csv(self):
        path = self._csv_field.model.get_value_as_string().strip()
        if not path:
            self._set_status("Set a CSV path first.")
            return
        try:
            n = self._host.export_csv(path)
        except Exception as error:
            self._set_status(f"CSV export failed: {error}")
            return
        self._set_status(f"Exported {n} rows to {os.path.basename(path)}" if n
                         else "Nothing recorded yet - tick 'Record' and run.")

    def _on_add_plot(self):
        key = self._selected_plot_key()
        if key and key not in self._plot_keys:
            self._plot_keys.append(key)
            self._rebuild_plots()

    def _on_clear_plots(self):
        self._plot_keys = []
        self._rebuild_plots()

    # -------------------------------------------------------------------- inputs
    def _rebuild_input_rows(self):
        self._inputs_container.clear()
        self._input_rows = []
        if self._host.instance_count == 0:
            with self._inputs_container:
                ui.Label("No FMUs attached.", style={"font_size": 12, "color": cl("#999999")})
            return
        try:
            bindings = self._host.input_bindings()
        except Exception as error:
            with self._inputs_container:
                ui.Label(f"Unavailable: {error}", style={"font_size": 12})
            return
        if not bindings:
            with self._inputs_container:
                ui.Label("This FMU has no mapped inputs in the stage.",
                         style={"font_size": 12, "color": cl("#999999")}, word_wrap=True)
            return

        with self._inputs_container:
            for b in bindings:
                with ui.HStack(height=22, spacing=4):
                    ui.Label(str(b["fmu_attribute"]), width=80, style={"font_size": 13})
                    field = ui.FloatField(width=110)
                    value = b["value"]
                    if isinstance(value, (int, float)):
                        field.model.set_value(float(value))
                    field.model.add_end_edit_fn(
                        lambda m, bb=b: self._write_input(bb, m.get_value_as_float()))
                    ui.Label(str(b["usd_attribute"]), style={"font_size": 11,
                                                             "color": cl("#8899AA")})
                    self._input_rows.append((b, field))

    def _write_input(self, binding, value):
        try:
            ok = self._host.set_usd_value(binding["prim_path"], binding["usd_attribute"], value)
            self._set_status(f"{binding['fmu_attribute']} = {value:g}" if ok
                             else f"Could not write {binding['usd_attribute']}")
        except Exception as error:
            self._set_status(f"Input write failed: {error}")

    def _refresh_input_values(self):
        if self._host.instance_count == 0:
            return
        try:
            current = {(b["prim_path"], b["usd_attribute"]): b["value"]
                       for b in self._host.input_bindings()}
        except Exception:
            return
        for binding, field in self._input_rows:
            value = current.get((binding["prim_path"], binding["usd_attribute"]))
            if isinstance(value, (int, float)):
                field.model.set_value(float(value))

    # ------------------------------------------------------------------- outputs
    def _rebuild_output_rows(self):
        self._outputs_container.clear()
        self._output_rows = {}
        rows = self._host.output_values() if self._host.instance_count else []
        if not rows:
            with self._outputs_container:
                ui.Label("No outputs yet - attach and step.",
                         style={"font_size": 12, "color": cl("#999999")})
            return
        with self._outputs_container:
            with ui.HStack(height=20):
                ui.Label("Variable", width=80, style={"font_size": 12, "color": cl("#BBBBBB")})
                ui.Label("Prim", width=90, style={"font_size": 12, "color": cl("#BBBBBB")})
                ui.Label("Value", style={"font_size": 12, "color": cl("#BBBBBB")})
            for r in rows:
                key = (r["fmu_attribute"], r["prim_path"])
                with ui.HStack(height=18):
                    ui.Label(str(r["fmu_attribute"]), width=80, style={"font_size": 12})
                    ui.Label(r["prim_path"].split("/")[-1], width=90,
                             style={"font_size": 11, "color": cl("#8899AA")})
                    self._output_rows[key] = ui.Label(self._fmt(r["value"]),
                                                      style={"font_size": 12})
        self._output_keys = [(r["fmu_attribute"], r["prim_path"]) for r in rows]

    def _refresh_output_values(self):
        rows = self._host.output_values()
        keys = [(r["fmu_attribute"], r["prim_path"]) for r in rows]
        if keys != self._output_keys:        # set changed -> rebuild once
            self._rebuild_output_rows()
            return
        for r in rows:
            label = self._output_rows.get((r["fmu_attribute"], r["prim_path"]))
            if label is not None:
                label.text = self._fmt(r["value"])

    @staticmethod
    def _fmt(value):
        if isinstance(value, (int, float)):
            return f"{value:.4g}"
        return str(value)

    # --------------------------------------------------------------------- plots
    def _rebuild_plot_selector(self):
        self._plot_container.clear()
        keys = self._host.history_keys()
        with self._plot_container:
            self._plot_combo = ui.ComboBox(0, *keys) if keys else ui.ComboBox(0, "(record first)")
        self._plot_selector_keys = keys

    def _selected_plot_key(self):
        keys = getattr(self, "_plot_selector_keys", [])
        combo = getattr(self, "_plot_combo", None)
        if not keys or combo is None:
            return None
        idx = combo.model.get_item_value_model().as_int
        return keys[idx] if 0 <= idx < len(keys) else None

    def _rebuild_plots(self):
        self._plots_area.clear()
        self._plots = {}
        if not self._plot_keys:
            return
        with self._plots_area:
            for i, key in enumerate(self._plot_keys):
                color = _PLOT_COLORS[i % len(_PLOT_COLORS)]
                with ui.VStack(height=0, spacing=1):
                    with ui.HStack(height=16):
                        ui.Label(key, width=70, style={"font_size": 12, "color": cl(color)})
                        rng = ui.Label("", style={"font_size": 11, "color": cl("#999999")})
                    plot = ui.Plot(ui.Type.LINE2D, height=70,
                                   style={"color": cl(color), "line_width": 2.0,
                                          "background_color": cl("#1C1C2E")})
                    self._plots[key] = (plot, rng)

    def _refresh_plots(self):
        for key, (plot, rng) in self._plots.items():
            times, values = self._host.history_series(key)
            if not values:
                continue
            lo, hi = min(values), max(values)
            if hi - lo < 1e-9:
                hi = lo + 1.0
            plot.set_xy_data([(t, v) for t, v in zip(times, values)])
            plot.scale_min = lo
            plot.scale_max = hi
            rng.text = f"{lo:.4g} .. {hi:.4g}   (t {times[0]:.1f}-{times[-1]:.1f}s)"

    # ------------------------------------------------------------------ stepping
    def _on_update(self, _event):
        if not self._playing or self._host.instance_count == 0:
            return
        dt = max(1e-4, self._dt_field.model.get_value_as_float())

        if not self._realtime.model.get_value_as_bool():
            self._do_step(dt)
            return

        speed = max(0.01, self._speed_field.model.get_value_as_float())
        now = time.perf_counter()
        elapsed = now - (self._last_wall if self._last_wall is not None else now)
        self._last_wall = now
        self._accum += elapsed * speed

        substeps = 0
        while self._accum >= dt and substeps < _MAX_SUBSTEPS:
            self._accum -= dt
            substeps += 1
        if self._accum > dt * _MAX_SUBSTEPS:
            self._accum = 0.0
        if substeps:
            self._do_step(dt, substeps=substeps)

    def _do_step(self, dt=None, substeps=1):
        if self._host.instance_count == 0:
            self._set_status("No FMUs attached.")
            return
        dt = dt if dt is not None else max(1e-4, self._dt_field.model.get_value_as_float())
        try:
            for _ in range(max(1, int(substeps))):
                self._host.step(dt)
        except Exception as error:
            self._playing = False
            self._play_button.text = "Play"
            self._set_status(f"Step failed: {error}")
            self._refresh_log()
            return
        self._refresh_dynamic()

    def _refresh_dynamic(self, force=False):
        """Throttled refresh of everything that changes while stepping."""
        now = time.perf_counter()
        if not force and (now - self._last_ui_refresh) < _UI_REFRESH_PERIOD:
            return
        self._last_ui_refresh = now

        self._time_label.text = f"t = {self._host.time:.3f} s"
        self._refresh_attr_combo()
        self._apply_coloring()
        self._refresh_output_values()
        if self._host.recording:
            self._history_label.text = f"{len(self._host.history)} rows"
            if not getattr(self, "_plot_selector_keys", None):
                self._rebuild_plot_selector()
        self._refresh_plots()

    # ------------------------------------------------------------------ coloring
    def _ensure_coloring(self):
        if self._coloring is None:
            from .coloring import FmiColoring
            self._coloring = FmiColoring(self._selected_cmap(), "vertical")
            self._on_font_changed()
        return self._coloring

    def _selected_cmap(self):
        idx = self._cmap_combo.model.get_item_value_model().as_int
        return self._cmaps[idx] if 0 <= idx < len(self._cmaps) else self._cmaps[0]

    def _refresh_attr_combo(self):
        attrs = self._host.output_attributes()
        if not attrs or attrs == self._attr_options:
            return
        # omni.ui ComboBox items cannot be mutated on every Kit version, so the
        # widget is rebuilt inside its container when the attribute set changes.
        self._attr_options = attrs
        self._attr_container.clear()
        with self._attr_container:
            self._attr_combo = ui.ComboBox(0, *attrs)

    def _selected_attribute(self):
        if not self._attr_options or self._attr_combo is None:
            return None
        idx = self._attr_combo.model.get_item_value_model().as_int
        return self._attr_options[idx] if 0 <= idx < len(self._attr_options) else self._attr_options[0]

    def _apply_coloring(self):
        attr = self._selected_attribute()
        if not attr:
            return
        coloring = self._ensure_coloring()
        coloring.set_attribute(attr)
        vmin = vmax = None
        if self._manual.model.get_value_as_bool():
            vmin = self._parse(self._min_field)
            vmax = self._parse(self._max_field)
        label = self._label_field.model.get_value_as_string().strip() or attr
        units = self._units_field.model.get_value_as_string().strip()
        if units:
            label = f"{label} ({units})"
        coloring.update(self._host, vmin, vmax, label=label)

    @staticmethod
    def _parse(field):
        text = field.model.get_value_as_string().strip()
        try:
            return float(text) if text else None
        except ValueError:
            return None

    # ---------------------------------------------------------------------- misc
    def _set_status(self, text):
        if getattr(self, "_status", None) is not None:
            self._status.text = str(text)

    def _refresh_log(self):
        if getattr(self, "_log_field", None) is not None:
            self._log_field.model.set_value(self._host.log_text())
