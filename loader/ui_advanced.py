"""Advanced: how a launch is driven, and what it logs.

Everything here is off the happy path on purpose. The default boot strategy is
the only one verified to land the right map; the others are experiments kept
switchable so they can be re-tested later rather than rediscovered. The panel
says which is which rather than presenting them as equals.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import settings, strategies  # noqa: E402


def _heading(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text.upper(), xalign=0.0)
    label.get_style_context().add_class("pack-count")
    label.set_margin_top(14)
    return label


class AdvancedDialog(Gtk.Dialog):
    def __init__(self, parent):
        super().__init__(title="Advanced", transient_for=parent, modal=True)
        self.set_default_size(640, 620)
        self.add_button("Close", Gtk.ResponseType.CLOSE)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(20)
        box.set_margin_end(20)
        scroller.add(box)
        self.get_content_area().pack_start(scroller, True, True, 0)

        current = settings.load()

        # -- boot strategy --------------------------------------------------
        box.pack_start(_heading("how a map is reached"), False, False, 0)
        chosen = current.get("boot_strategy", "macro")
        group = None
        for strategy in strategies.BOOT_STRATEGIES:
            button = Gtk.RadioButton.new_with_label_from_widget(group, strategy.name)
            group = group or button
            button.set_active(strategy.id == chosen)
            button.connect("toggled", self._on_strategy, strategy.id)
            box.pack_start(button, False, False, 0)
            detail = Gtk.Label(xalign=0.0, wrap=True, label=strategy.detail)
            detail.set_max_width_chars(70)
            detail.get_style_context().add_class("pack-count")
            detail.set_margin_start(26)
            detail.set_margin_bottom(8)
            box.pack_start(detail, False, False, 0)

        # -- diagnostics ----------------------------------------------------
        box.pack_start(_heading("diagnostics"), False, False, 0)
        enabled = set(current.get("diagnostics", []))
        for entry in strategies.DIAGNOSTICS:
            check = Gtk.CheckButton(label=entry.name)
            check.set_active(entry.id in enabled)
            check.connect("toggled", self._on_diagnostic, entry.id)
            box.pack_start(check, False, False, 0)
            detail = Gtk.Label(xalign=0.0, wrap=True, label=entry.detail)
            detail.set_max_width_chars(70)
            detail.get_style_context().add_class("pack-count")
            detail.set_margin_start(26)
            detail.set_margin_bottom(8)
            box.pack_start(detail, False, False, 0)

        # -- timings --------------------------------------------------------
        box.pack_start(_heading("menu timings"), False, False, 0)
        note = Gtk.Label(xalign=0.0, wrap=True, label=(
            "Measured defaults - the evidence for each is in loader/navigate.py. "
            "Raise them if a map lands somewhere unexpected on a slower machine; "
            "the old, slower values were 1500 / 2500 / 260 / 1200."
        ))
        note.set_max_width_chars(70)
        note.get_style_context().add_class("pack-count")
        box.pack_start(note, False, False, 6)

        timings = dict(current.get("timings", {}))
        for env, label_text, default, hint in strategies.TIMINGS:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            label = Gtk.Label(xalign=0.0, label=label_text)
            label.set_size_request(170, -1)
            row.pack_start(label, False, False, 0)
            spin = Gtk.SpinButton.new_with_range(0, 5000, 20)
            spin.set_value(int(timings.get(env, default)))
            spin.connect("value-changed", self._on_timing, env, default)
            row.pack_start(spin, False, False, 0)
            hint_label = Gtk.Label(xalign=0.0, label=hint)
            hint_label.get_style_context().add_class("pack-count")
            row.pack_start(hint_label, True, True, 0)
            box.pack_start(row, False, False, 0)

        reset = Gtk.Button(label="Reset timings to the measured defaults")
        reset.set_halign(Gtk.Align.START)
        reset.set_margin_top(10)
        reset.connect("clicked", self._on_reset)
        box.pack_start(reset, False, False, 0)

        self.show_all()

    # -- persistence -------------------------------------------------------

    def _on_strategy(self, button, strategy_id: str) -> None:
        if button.get_active():
            settings.save({"boot_strategy": strategy_id})

    def _on_diagnostic(self, button, entry_id: str) -> None:
        enabled = set(settings.load().get("diagnostics", []))
        enabled.add(entry_id) if button.get_active() else enabled.discard(entry_id)
        settings.save({"diagnostics": sorted(enabled)})

    def _on_timing(self, spin, env: str, default: int) -> None:
        timings = dict(settings.load().get("timings", {}))
        value = int(spin.get_value())
        if value == default:
            timings.pop(env, None)
        else:
            timings[env] = value
        settings.save({"timings": timings})

    def _on_reset(self, _button) -> None:
        settings.save({"timings": {}})
        self.response(Gtk.ResponseType.CLOSE)
