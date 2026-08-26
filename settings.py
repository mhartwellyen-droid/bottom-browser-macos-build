"""Persistent settings and the settings surface for Bottom Browser."""

from __future__ import annotations

from dataclasses import dataclass, asdict

from PyQt6.QtCore import QSettings, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout


@dataclass(frozen=True)
class BrowserPreferences:
    block_ads: bool = True
    block_trackers: bool = True
    youtube_dislikes: bool = False
    bottom_ai: bool = True
    battery_saver: bool = True
    background_indexing: bool = True
    sidebar_open: bool = False


class SettingsStore:
    """QSettings-backed preferences shared by every browser control."""

    def __init__(self) -> None:
        self._settings = QSettings("Bottom Browser", "Bottom Browser")

    def snapshot(self) -> BrowserPreferences:
        defaults = BrowserPreferences()
        values = {}
        for key, default in asdict(defaults).items():
            raw = self._settings.value(key, default)
            values[key] = (
                raw.lower() in {"1", "true", "yes", "on"}
                if isinstance(raw, str)
                else bool(raw)
            )
        return BrowserPreferences(**values)

    def set(self, key: str, enabled: bool) -> BrowserPreferences:
        if key not in asdict(BrowserPreferences()):
            raise KeyError(key)
        self._settings.setValue(key, bool(enabled))
        self._settings.sync()
        return self.snapshot()


class SettingsDialog(QDialog):
    """Complete browser feature toggles with immediate persistence."""

    setting_changed = pyqtSignal(str, bool)

    LABELS = (
        ("block_ads", "Block ads"),
        ("block_trackers", "Block cross-site trackers"),
        (
            "youtube_dislikes",
            "Share watched video IDs with Return YouTube Dislike",
        ),
        ("bottom_ai", "Enable private on-device Bottom AI"),
        ("battery_saver", "Battery saver for inactive tabs"),
        ("background_indexing", "Allow background index refresh"),
        ("sidebar_open", "Open the companion sidebar at launch"),
    )

    def __init__(self, store: SettingsStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Bottom Browser settings")
        self.setMinimumWidth(470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(12)
        title = QLabel("Browser settings")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        intro = QLabel(
            "Privacy features and Bottom AI run locally. The AI model downloads "
            "once on first use. Page text is used only when you explicitly share it."
        )
        intro.setWordWrap(True)
        intro.setObjectName("secondaryText")
        layout.addWidget(intro)
        current = asdict(store.snapshot())
        for key, text in self.LABELS:
            toggle = QCheckBox(text)
            toggle.setChecked(current[key])
            toggle.toggled.connect(
                lambda enabled, name=key: self._change(name, enabled)
            )
            layout.addWidget(toggle)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _change(self, key: str, enabled: bool) -> None:
        self.store.set(key, enabled)
        self.setting_changed.emit(key, enabled)