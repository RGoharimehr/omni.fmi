# SPDX-FileCopyrightText: 2026 Reza Goharimehr <rgoharim@villanova.edu>
# SPDX-License-Identifier: Apache-2.0
"""Kit UI for omni.fmi: load a stage with FMI schema, step FMUs, color with fastcolor."""
import omni.ext
import omni.ui as ui
import omni.kit.app
import omni.usd

from .host import FmiUsdHost


class FmiUsdExtension(omni.ext.IExt):
    def on_startup(self, ext_id: str):
        self._host = FmiUsdHost()
        self._coloring = None          # created lazily (needs omni.fastcolor)
        self._playing = False
        self._accum = 0.0
        self._sub = None
        self._status = None
        self._attr_combo = None
        self._cmaps = []

        self._window = ui.Window("FMI on USD", width=420, height=520)
        with self._window.frame:
            self._build_ui()

        self._sub = (
            omni.kit.app.get_app()
            .get_update_event_stream()
            .create_subscription_to_pop(self._on_update, name="omni.fmi.step")
        )

    def get_host(self) -> FmiUsdHost:
        return self._host

    # ------------------------------------------------------------------------ UI
    def _build_ui(self):
        from omni.fastcolor import colormaps
        self._cmaps = colormaps.available()

        with ui.VStack(spacing=8, height=0):
            ui.Label("FMI on USD", style={"font_size": 18})

            with ui.HStack(height=24):
                ui.Label("Stage:", width=60)
                self._stage_field = ui.StringField()
                ui.Button("Open", width=60, clicked_fn=self._on_open_stage)
            with ui.HStack(height=26, spacing=6):
                ui.Button("Attach FMUs", clicked_fn=self._on_attach)
                ui.Button("Detach", clicked_fn=self._on_detach)
                ui.Button("Reset", clicked_fn=self._on_reset)

            ui.Separator(height=2)
            with ui.HStack(height=24):
                ui.Label("dt [s]:", width=60)
                self._dt_field = ui.FloatField(width=80)
                self._dt_field.model.set_value(1.0 / 60.0)
                ui.Spacer(width=10)
                self._play_button = ui.Button("Play", width=70, clicked_fn=self._on_toggle_play)
                ui.Button("Step", width=70, clicked_fn=self._on_step_once)

            ui.Separator(height=2)
            ui.Label("Coloring (omni.fastcolor)", style={"font_size": 16})
            with ui.HStack(height=24):
                ui.Label("Color by:", width=70)
                self._attr_container = ui.HStack()     # combo rebuilt in here after first step
                self._attr_combo = None
                self._attr_options = []
            with ui.HStack(height=24):
                ui.Label("Colormap:", width=70)
                self._cmap_combo = ui.ComboBox(0, *self._cmaps)
                self._cmap_combo.model.add_item_changed_fn(self._on_cmap_changed)
            with ui.HStack(height=24, spacing=4):
                self._manual = ui.CheckBox(width=18)
                ui.Label("Manual bounds", width=100)
                ui.Label("Min", width=26)
                self._min_field = ui.StringField(width=56)
                ui.Label("Max", width=26)
                self._max_field = ui.StringField(width=56)
            with ui.HStack(height=24, spacing=4):
                ui.Label("Legend:", width=70)
                self._orient_combo = ui.ComboBox(0, "vertical", "horizontal")
                self._orient_combo.model.add_item_changed_fn(self._on_orient_changed)

            ui.Separator(height=2)
            ui.Label("Log:", style={"font_size": 14})
            self._log_field = ui.StringField(multiline=True, height=140, read_only=True)
            self._status = ui.Label("Ready.", style={"font_size": 13})

    def _ensure_coloring(self):
        if self._coloring is None:
            from .coloring import FmiColoring
            self._coloring = FmiColoring(self._selected_cmap(), "vertical")
        return self._coloring

    def _selected_cmap(self) -> str:
        idx = self._cmap_combo.model.get_item_value_model().as_int
        return self._cmaps[idx] if 0 <= idx < len(self._cmaps) else self._cmaps[0]

    # ------------------------------------------------------------------- handlers
    def _on_open_stage(self):
        path = self._stage_field.model.get_value_as_string().strip()
        if path:
            omni.usd.get_context().open_stage(path)
            self._set_status(f"Opened stage: {path}")

    def _on_attach(self):
        try:
            n = self._host.attach()
            self._set_status(f"Attached {n} FMU instance(s).")
        except Exception as error:
            self._set_status(f"Attach failed: {error}")
        self._refresh_log()

    def _on_detach(self):
        self._playing = False
        self._play_button.text = "Play"
        self._host.detach()
        if self._coloring:
            self._coloring.clear()
        self._set_status("Detached.")

    def _on_reset(self):
        self._host.reset()
        self._set_status("Reset to t=0.")

    def _on_toggle_play(self):
        self._playing = not self._playing
        self._play_button.text = "Pause" if self._playing else "Play"

    def _on_step_once(self):
        self._do_step()

    def _on_cmap_changed(self, *args):
        if self._coloring:
            self._coloring.set_colormap(self._selected_cmap())

    def _on_orient_changed(self, *args):
        idx = self._orient_combo.model.get_item_value_model().as_int
        if self._coloring:
            self._coloring.set_orientation("horizontal" if idx == 1 else "vertical")

    # --------------------------------------------------------------------- stepping
    def _on_update(self, _event):
        if not self._playing or self._host.instance_count == 0:
            return
        dt = max(1e-4, self._dt_field.model.get_value_as_float())
        self._do_step(dt)

    def _do_step(self, dt=None):
        if self._host.instance_count == 0:
            self._set_status("No FMUs attached.")
            return
        dt = dt if dt is not None else max(1e-4, self._dt_field.model.get_value_as_float())
        try:
            t = self._host.step(dt)
        except Exception as error:
            self._playing = False
            self._play_button.text = "Play"
            self._set_status(f"Step failed: {error}")
            self._refresh_log()
            return

        self._refresh_attr_combo()
        self._apply_coloring()
        self._set_status(f"t = {t:.3f} s")

    def _refresh_attr_combo(self):
        attrs = self._host.output_attributes()
        if not attrs or attrs == getattr(self, "_attr_options", []):
            return
        # omni.ui ComboBox items can't be mutated (no model.clear on all Kit
        # versions), so rebuild the whole widget inside its container.
        self._attr_options = attrs
        self._attr_container.clear()
        with self._attr_container:
            self._attr_combo = ui.ComboBox(0, *attrs)

    def _selected_attribute(self):
        attrs = getattr(self, "_attr_options", [])
        if not attrs or self._attr_combo is None:
            return None
        idx = self._attr_combo.model.get_item_value_model().as_int
        return attrs[idx] if 0 <= idx < len(attrs) else attrs[0]

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
        coloring.update(self._host, vmin, vmax, label=attr)

    @staticmethod
    def _parse(field):
        text = field.model.get_value_as_string().strip()
        try:
            return float(text) if text else None
        except ValueError:
            return None

    # ----------------------------------------------------------------------- misc
    def _set_status(self, text):
        if self._status is not None:
            self._status.text = text

    def _refresh_log(self):
        if self._log_field is not None:
            self._log_field.model.set_value(self._host.log_text())

    def on_shutdown(self):
        self._playing = False
        if self._sub:
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
