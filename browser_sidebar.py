"""AI chat, privacy status, and extension controls for Bottom Browser."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class BrowserSidebar(QWidget):
    ai_requested = pyqtSignal(str)
    page_share_requested = pyqtSignal()
    setting_changed = pyqtSignal(str, bool)
    settings_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("browserSidebar")
        self.setMinimumWidth(310)
        self.setMaximumWidth(380)
        self.toggles: dict[str, QCheckBox] = {}
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(13)
        header = QHBoxLayout()
        title = QLabel("BOTTOM COMPANION")
        title.setObjectName("eyebrow")
        close = QPushButton("×")
        close.setObjectName("sidebarClose")
        close.setToolTip("Close companion sidebar")
        close.clicked.connect(self.hide)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(close)
        layout.addLayout(header)

        ai = QFrame()
        ai.setObjectName("aiHero")
        ai_layout = QVBoxLayout(ai)
        ai_layout.setContentsMargins(16, 16, 16, 16)
        ai_layout.setSpacing(9)
        label = QLabel("Bottom AI")
        label.setObjectName("heroTitle")
        note = QLabel(
            "Runs privately on this Mac. The model downloads once on first use."
        )
        note.setWordWrap(True)
        note.setObjectName("secondaryText")
        self.ai_status = QLabel("On-device · no account or API key")
        self.ai_status.setObjectName("secondaryText")
        self.transcript = QTextBrowser()
        self.transcript.setObjectName("aiTranscript")
        self.transcript.setPlaceholderText("Answers will appear here.")
        share_page = QPushButton("Share page text & summarize")
        share_page.setObjectName("quietAction")
        share_page.setToolTip(
            "Explicitly give up to 6,000 characters from this page to the local model"
        )
        share_page.clicked.connect(self.page_share_requested)
        row = QHBoxLayout()
        self.prompt = QLineEdit()
        self.prompt.setObjectName("aiPrompt")
        self.prompt.setPlaceholderText("Ask anything…")
        self.prompt.returnPressed.connect(self._send)
        self.send_button = QPushButton("Ask")
        self.send_button.setObjectName("primaryAction")
        self.send_button.clicked.connect(self._send)
        row.addWidget(self.prompt, 1)
        row.addWidget(self.send_button)
        ai_layout.addWidget(label)
        ai_layout.addWidget(note)
        ai_layout.addWidget(self.ai_status)
        ai_layout.addWidget(share_page)
        ai_layout.addWidget(self.transcript, 1)
        ai_layout.addLayout(row)
        layout.addWidget(ai, 1)

        status = QFrame()
        status.setObjectName("sidebarCard")
        status_layout = QVBoxLayout(status)
        status_layout.setContentsMargins(14, 12, 14, 12)
        status_layout.setSpacing(8)
        status_layout.addWidget(self._section_label("PRIVACY & POWER"))
        self.block_status = QLabel("No requests blocked on this page")
        self.block_status.setObjectName("secondaryText")
        status_layout.addWidget(self.block_status)
        for key, text in (
            ("block_ads", "Ad blocker"),
            ("block_trackers", "Tracker blocker"),
            ("youtube_dislikes", "YouTube dislikes (shares video ID)"),
            ("battery_saver", "Battery saver"),
        ):
            toggle = QCheckBox(text)
            toggle.toggled.connect(
                lambda enabled, name=key: self.setting_changed.emit(name, enabled)
            )
            self.toggles[key] = toggle
            status_layout.addWidget(toggle)
        layout.addWidget(status)

        settings = QPushButton("Open all settings")
        settings.setObjectName("footerAction")
        settings.clicked.connect(self.settings_requested)
        layout.addWidget(settings)

    def _send(self) -> None:
        text = self.prompt.text().strip()
        if text:
            self.prompt.clear()
            self.ai_requested.emit(text)

    def apply_preferences(self, values) -> None:
        for key, toggle in self.toggles.items():
            toggle.blockSignals(True)
            toggle.setChecked(bool(getattr(values, key)))
            toggle.blockSignals(False)

    def set_ai_busy(self, busy: bool) -> None:
        self.send_button.setDisabled(busy)
        self.send_button.setText("Thinking…" if busy else "Ask")
        if not busy:
            self.ai_status.setText("On-device · no account or API key")

    def set_ai_progress(self, status: str) -> None:
        self.ai_status.setText(status)

    def show_ai_answer(self, prompt: str, answer: str, *, error: bool = False) -> None:
        color = "#ff9bbd" if error else "#edf0f8"
        self.transcript.append(
            f'<p style="color:#8ddce5"><b>You</b><br>{self._escape(prompt)}</p>'
            f'<p style="color:{color}"><b>Bottom AI</b><br>'
            f'{self._escape(answer).replace(chr(10), "<br>")}</p>'
        )

    def set_block_counts(self, ads: int, trackers: int) -> None:
        total = ads + trackers
        self.block_status.setText(
            f"{total} blocked · {ads} ads · {trackers} trackers"
            if total
            else "No requests blocked on this page"
        )

    @staticmethod
    def _escape(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def toggle(self) -> None:
        self.setVisible(not self.isVisible())