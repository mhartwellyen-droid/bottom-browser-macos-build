"""Bottom Browser — a modern PyQt6 browser with bottom-mounted controls."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs

from PyQt6.QtCore import (
    QByteArray,
    QBuffer,
    QCoreApplication,
    QIODevice,
    QSize,
    QTimer,
    Qt,
    QUrl,
)
from PyQt6.QtGui import (
    QAction,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from browser_utils import display_url, normalize_user_input
from browser_sidebar import BrowserSidebar
from ai_client import AIRequestThread
from privacy import PrivacyRequestInterceptor, youtube_dislike_injection_js
from search_engine import Crawler, LocalSearchEngine
from search_pages import render_error, render_new_tab, render_results
from settings import SettingsDialog, SettingsStore


APP_NAME = "Bottom Browser"
APP_VERSION = "3.0.0"
SEARCH_SEEDS = (
    "https://en.wikipedia.org/wiki/Main_Page",
    "https://docs.python.org/3/",
    "https://developer.mozilla.org/en-US/docs/Web",
)


ICON_SVGS = {
    "app": """
      <rect width="64" height="64" rx="19" fill="url(#g)"/>
      <defs><linearGradient id="g" x1="7" y1="4" x2="58" y2="60">
      <stop stop-color="#7C5CFF"/><stop offset="1" stop-color="#2EC6FF"/>
      </linearGradient></defs>
      <path d="M45 21a18 18 0 1 0 1.7 18" fill="none" stroke="white"
      stroke-width="7" stroke-linecap="round"/>
    """,
    "back": '<path d="M20 7 9 18l11 11M10 18h21" fill="none" stroke="#CDD3E2" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "forward": '<path d="m16 7 11 11-11 11m10-11H5" fill="none" stroke="#CDD3E2" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "refresh": '<path d="M27 12a12 12 0 1 0 1.4 10M27 12V5m0 7h-7" fill="none" stroke="#CDD3E2" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>',
    "stop": '<rect x="9" y="9" width="18" height="18" rx="3" fill="#CDD3E2"/>',
    "plus": '<path d="M18 7v22M7 18h22" fill="none" stroke="#CDD3E2" stroke-width="2.6" stroke-linecap="round"/>',
    "menu": '<circle cx="8" cy="18" r="2.2" fill="#CDD3E2"/><circle cx="18" cy="18" r="2.2" fill="#CDD3E2"/><circle cx="28" cy="18" r="2.2" fill="#CDD3E2"/>',
    "shield": '<path d="M18 4 7.5 8v8.5c0 6.7 4.4 11.8 10.5 15 6.1-3.2 10.5-8.3 10.5-15V8L18 4Z" fill="none" stroke="#8690A7" stroke-width="2.1" stroke-linejoin="round"/><path d="m13.5 18 3 3 6-7" fill="none" stroke="#43D6A5" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
}


def make_icon(name: str, size: int = 36) -> QIcon:
    """Render a crisp SVG icon without requiring external asset files."""
    content = ICON_SVGS[name]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" '
        f'viewBox="0 0 36 36">{content}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def resource_path(filename: str) -> Path:
    """Locate a bundled PyInstaller resource or a source-checkout file."""
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / filename


def application_icon() -> QIcon:
    """Use the same SVG source at runtime that produces the macOS ICNS."""
    path = resource_path("assets/BottomBrowser.svg")
    return QIcon(str(path)) if path.is_file() else make_icon("app", 64)


def register_bottom_scheme() -> None:
    """Register the private internal-page scheme before QApplication exists."""
    scheme = QWebEngineUrlScheme(QByteArray(b"bottom"))
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.LocalScheme
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)


class BottomSchemeHandler(QWebEngineUrlSchemeHandler):
    """Serve new-tab and search pages directly from the local search index."""

    def __init__(self, engine: LocalSearchEngine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine

    def requestStarted(self, job) -> None:  # noqa: N802 (Qt API)
        try:
            url = job.requestUrl()
            page = url.host().lower()
            if page == "newtab":
                content = render_new_tab(self.engine.stats())
            elif page == "search":
                query = parse_qs(url.query()).get("q", [""])[0].strip()
                started = time.perf_counter()
                results = self.engine.search(query, limit=20) if query else []
                elapsed_ms = (time.perf_counter() - started) * 1000
                content = render_results(
                    query, results, self.engine.stats(), elapsed_ms
                )
            else:
                content = render_error("That internal Bottom page does not exist.")
        except Exception as exc:
            content = render_error(f"The private index returned an error: {exc}")

        payload = QByteArray(content.encode("utf-8"))
        buffer = QBuffer(job)
        buffer.setData(payload)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(QByteArray(b"text/html; charset=utf-8"), buffer)


class BrowserView(QWebEngineView):
    """A web view that sends popup windows into a new browser tab."""

    def __init__(self, window: "BrowserWindow") -> None:
        super().__init__()
        self.browser_window = window

    def createWindow(
        self, window_type: QWebEnginePage.WebWindowType
    ) -> "BrowserView":
        del window_type
        return self.browser_window.add_new_tab(switch=True)


class BrowserTabBar(QTabBar):
    """Tab bar with middle-click close behavior."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMovable(True)
        self.setTabsClosable(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setExpanding(False)
        self.setDocumentMode(True)
        self.setUsesScrollButtons(True)

    def tabSizeHint(self, index: int) -> QSize:  # noqa: N802 (Qt API)
        hint = super().tabSizeHint(index)
        return QSize(min(max(hint.width(), 150), 230), 38)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.MiddleButton:
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self.tabCloseRequested.emit(index)
                return
        super().mouseReleaseEvent(event)


class BrowserWindow(QMainWindow):
    """Main window with all browsing controls anchored to the bottom."""

    def __init__(self) -> None:
        super().__init__()
        self.closed_tabs: list[str] = []
        self._is_loading = False
        self._download_count = 0
        self.search_crawler: Crawler | None = None
        self._ai_requests: set[AIRequestThread] = set()
        self._ai_busy_owner: AIRequestThread | None = None
        self.settings_store = SettingsStore()
        self.preferences = self.settings_store.snapshot()

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(application_icon())
        self.resize(1280, 820)
        self.setMinimumSize(760, 520)

        self.data_root = Path.home() / ".bottom-browser"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.search_engine = LocalSearchEngine(
            self.data_root / "bottom-search.sqlite",
            resource_path("starter_corpus.json"),
        )
        self.profile = QWebEngineProfile.defaultProfile()
        self._configure_profile()
        self.privacy_interceptor = PrivacyRequestInterceptor(
            self,
            block_ads=self.preferences.block_ads,
            block_trackers=self.preferences.block_trackers,
        )
        self.profile.setUrlRequestInterceptor(self.privacy_interceptor)
        self.scheme_handler = BottomSchemeHandler(self.search_engine, self)
        self.profile.installUrlSchemeHandler(b"bottom", self.scheme_handler)

        self.crawl_timer = QTimer(self)
        self.crawl_timer.setInterval(1000)
        self.crawl_timer.timeout.connect(self._check_crawler)
        self.privacy_timer = QTimer(self)
        self.privacy_timer.setInterval(750)
        self.privacy_timer.timeout.connect(self._update_privacy_status)
        self.privacy_timer.start()

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.South)
        self.tabs.setTabBar(BrowserTabBar(self.tabs))
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._current_tab_changed)

        self.new_tab_button = QPushButton()
        self.new_tab_button.setObjectName("newTabButton")
        self.new_tab_button.setIcon(make_icon("plus"))
        self.new_tab_button.setIconSize(QSize(18, 18))
        self.new_tab_button.setToolTip("New tab (Ctrl+T)")
        self.new_tab_button.clicked.connect(lambda: self.add_new_tab())
        self.tabs.setCornerWidget(
            self.new_tab_button, Qt.Corner.BottomRightCorner
        )

        self.progress = QProgressBar()
        self.progress.setObjectName("loadProgress")
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(2)
        self.progress.hide()

        self.bottom_bar = self._build_bottom_bar()
        self.sidebar = BrowserSidebar(self)
        self.sidebar.setVisible(self.preferences.sidebar_open)
        self.sidebar.apply_preferences(self.preferences)
        self.sidebar.ai_requested.connect(self._handle_ai_request)
        self.sidebar.page_share_requested.connect(self._share_current_page)
        self.sidebar.setting_changed.connect(self._apply_setting)
        self.sidebar.settings_requested.connect(self._show_settings)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        browser_column = QVBoxLayout()
        browser_column.setContentsMargins(0, 0, 0, 0)
        browser_column.setSpacing(0)
        browser_column.addWidget(self.tabs, 1)
        browser_column.addWidget(self.progress)
        browser_column.addWidget(self.bottom_bar)
        content.addLayout(browser_column, 1)
        content.addWidget(self.sidebar)
        layout.addLayout(content)
        self.setCentralWidget(root)

        self.setStyleSheet(self._stylesheet())
        self._install_shortcuts()
        self.add_new_tab()

    def _configure_profile(self) -> None:
        self.profile.setPersistentStoragePath(str(self.data_root / "profile"))
        self.profile.setCachePath(str(self.data_root / "cache"))
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self.profile.setHttpUserAgent(
            f"{self.profile.httpUserAgent()} {APP_NAME.replace(' ', '')}/{APP_VERSION}"
        )
        self.profile.downloadRequested.connect(self._handle_download)

    def _build_bottom_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("bottomBar")
        bar.setFixedHeight(70)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        self.back_button = self._icon_button("back", "Back (Alt+Left)")
        self.forward_button = self._icon_button(
            "forward", "Forward (Alt+Right)"
        )
        self.reload_button = self._icon_button("refresh", "Refresh (Ctrl+R)")
        self.back_button.clicked.connect(self.go_back)
        self.forward_button.clicked.connect(self.go_forward)
        self.reload_button.clicked.connect(self.reload_or_stop)

        self.address_frame = QFrame()
        self.address_frame.setObjectName("addressFrame")
        address_layout = QHBoxLayout(self.address_frame)
        address_layout.setContentsMargins(10, 0, 12, 0)
        address_layout.setSpacing(7)

        self.security_icon = QLabel()
        self.security_icon.setPixmap(make_icon("shield", 20).pixmap(20, 20))
        self.security_icon.setToolTip("Connection information")

        self.address_bar = QLineEdit()
        self.address_bar.setObjectName("addressBar")
        self.address_bar.setPlaceholderText("Search or enter address")
        self.address_bar.setClearButtonEnabled(True)
        self.address_bar.returnPressed.connect(self.navigate_from_address)

        address_layout.addWidget(self.security_icon)
        address_layout.addWidget(self.address_bar, 1)

        self.download_label = QLabel()
        self.download_label.setObjectName("downloadLabel")
        self.download_label.hide()

        self.menu_button = self._icon_button("menu", "Browser menu")
        self.menu_button.clicked.connect(self._show_menu)

        self.ai_button = QPushButton("AI")
        self.ai_button.setObjectName("accentControl")
        self.ai_button.setToolTip("Toggle AI companion")
        self.ai_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ai_button.clicked.connect(self.toggle_sidebar)

        self.extensions_button = QPushButton("Controls")
        self.extensions_button.setObjectName("controlButton")
        self.extensions_button.setToolTip("Extensions and privacy controls")
        self.extensions_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.extensions_button.clicked.connect(self._show_extensions)

        self.close_tab_button = QPushButton("Close")
        self.close_tab_button.setObjectName("closeTabButton")
        self.close_tab_button.setToolTip("Close current tab (Ctrl+W)")
        self.close_tab_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_tab_button.clicked.connect(self.close_current_tab)

        layout.addWidget(self.back_button)
        layout.addWidget(self.forward_button)
        layout.addWidget(self.reload_button)
        layout.addWidget(self.address_frame, 1)
        layout.addWidget(self.download_label)
        layout.addWidget(self.ai_button)
        layout.addWidget(self.extensions_button)
        layout.addWidget(self.close_tab_button)
        layout.addWidget(self.menu_button)
        return bar

    def _icon_button(self, icon_name: str, tooltip: str) -> QPushButton:
        button = QPushButton()
        button.setProperty("navButton", True)
        button.setIcon(make_icon(icon_name))
        button.setIconSize(QSize(20, 20))
        button.setFixedSize(42, 42)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _install_shortcuts(self) -> None:
        shortcuts = [
            ("Ctrl+L", self.focus_address),
            ("Ctrl+T", self.add_new_tab),
            ("Ctrl+W", self.close_current_tab),
            ("Ctrl+Shift+T", self.reopen_closed_tab),
            ("Ctrl+R", self.reload_or_stop),
            ("F5", self.reload_or_stop),
            ("Alt+Left", self.go_back),
            ("Alt+Right", self.go_forward),
            ("Ctrl++", lambda: self.change_zoom(0.1)),
            ("Ctrl+=", lambda: self.change_zoom(0.1)),
            ("Ctrl+-", lambda: self.change_zoom(-0.1)),
            ("Ctrl+0", self.reset_zoom),
            ("F11", self.toggle_fullscreen),
        ]
        self._shortcuts: list[QShortcut] = []
        for sequence, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    def add_new_tab(
        self, url: str | QUrl | None = None, switch: bool = True
    ) -> BrowserView:
        browser = BrowserView(self)
        browser.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        settings = browser.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True
        )
        force_dark = getattr(
            QWebEngineSettings.WebAttribute, "ForceDarkMode", None
        )
        if force_dark is not None:
            settings.setAttribute(force_dark, True)

        browser.titleChanged.connect(
            lambda title, view=browser: self._update_tab_title(view, title)
        )
        browser.iconChanged.connect(
            lambda icon, view=browser: self._update_tab_icon(view, icon)
        )
        browser.urlChanged.connect(
            lambda new_url, view=browser: self._on_url_changed(view, new_url)
        )
        browser.loadStarted.connect(
            lambda view=browser: self._on_load_started(view)
        )
        browser.loadProgress.connect(
            lambda value, view=browser: self._on_load_progress(view, value)
        )
        browser.loadFinished.connect(
            lambda ok, view=browser: self._on_load_finished(view, ok)
        )
        browser.page().windowCloseRequested.connect(
            lambda view=browser: self.close_view(view)
        )
        browser.page().renderProcessTerminated.connect(
            lambda status, code, view=browser: self._render_process_crashed(
                view, status, code
            )
        )

        index = self.tabs.addTab(browser, application_icon(), "New Tab")
        self.tabs.setTabToolTip(index, "New Tab")
        if switch:
            self.tabs.setCurrentIndex(index)

        if url is None:
            browser.setProperty("isNewTab", True)
            browser.setUrl(QUrl("bottom://newtab/"))
            if switch:
                self.address_bar.clear()
                self.address_bar.setFocus()
        else:
            browser.setProperty("isNewTab", False)
            browser.setUrl(url if isinstance(url, QUrl) else QUrl(url))
        return browser

    def close_tab(self, index: int) -> None:
        if index < 0:
            return
        view = self.tabs.widget(index)
        if isinstance(view, QWebEngineView):
            url = view.url().toString()
            if url and url != "about:blank":
                self.closed_tabs.append(url)
                self.closed_tabs = self.closed_tabs[-20:]
        self.tabs.removeTab(index)
        view.deleteLater()
        if self.tabs.count() == 0:
            self.add_new_tab()

    def close_view(self, view: BrowserView) -> None:
        index = self.tabs.indexOf(view)
        if index >= 0:
            self.close_tab(index)

    def close_current_tab(self) -> None:
        self.close_tab(self.tabs.currentIndex())

    def reopen_closed_tab(self) -> None:
        if self.closed_tabs:
            self.add_new_tab(self.closed_tabs.pop())

    def current_browser(self) -> BrowserView | None:
        browser = self.tabs.currentWidget()
        return browser if isinstance(browser, BrowserView) else None

    def navigate_from_address(self) -> None:
        browser = self.current_browser()
        if browser is None:
            return
        target = normalize_user_input(self.address_bar.text())
        if not target:
            return
        browser.setProperty("isNewTab", False)
        browser.setUrl(QUrl(target))

    def focus_address(self) -> None:
        self.address_bar.setFocus()
        self.address_bar.selectAll()

    def toggle_sidebar(self) -> None:
        self.sidebar.toggle()
        self.preferences = self.settings_store.set(
            "sidebar_open", self.sidebar.isVisible()
        )
        self.ai_button.setProperty("active", self.sidebar.isVisible())
        self.ai_button.style().unpolish(self.ai_button)
        self.ai_button.style().polish(self.ai_button)
        self.statusBar().showMessage(
            "AI companion opened." if self.sidebar.isVisible() else "AI companion hidden.",
            2200,
        )

    def _handle_ai_request(self, prompt: str) -> None:
        if not self.preferences.bottom_ai:
            self.sidebar.show_ai_answer(
                prompt, "Bottom AI is turned off in Settings.", error=True
            )
            return
        self._start_ai_request(prompt, self._search_context(prompt))

    def _share_current_page(self) -> None:
        """Share page text only after the dedicated, explicit user action."""
        browser = self.current_browser()
        if not browser:
            return
        if not self.preferences.bottom_ai:
            self.sidebar.show_ai_answer(
                "Summarize this page",
                "Bottom AI is turned off in Settings.",
                error=True,
            )
            return
        shared_url = browser.url().toString()
        shared_title = browser.title()
        browser.page().toPlainText(
            lambda text, view=browser, url=shared_url, title=shared_title:
            self._share_page_text(view, url, title, text)
        )

    def _share_page_text(
        self, browser: BrowserView, url: str, title: str, text: str
    ) -> None:
        if self.tabs.indexOf(browser) < 0 or browser.url().toString() != url:
            self.sidebar.show_ai_answer(
                "Summarize this page",
                "The page changed before its text could be shared. Try again.",
                error=True,
            )
            return
        self._start_ai_request(
            "Summarize the page text I explicitly shared.",
            [{"title": title[:300], "url": url[:1000], "snippet": text[:6000]}],
        )

    def _search_context(self, query: str) -> list[dict[str, str]]:
        return [
            {
                "title": result.title,
                "url": result.url,
                "snippet": result.description
                or result.snippet.replace("<mark>", "").replace("</mark>", ""),
            }
            for result in self.search_engine.search(
                query, limit=6, record_history=False
            )
        ]

    def _start_ai_request(
        self,
        prompt: str,
        context: list[dict[str, str]],
        target: BrowserView | None = None,
        target_url: str = "",
    ) -> None:
        self.sidebar.set_ai_busy(True)
        worker = AIRequestThread(prompt, context, parent=self)
        self._ai_busy_owner = worker
        self._ai_requests.add(worker)
        worker.completed.connect(
            lambda answer, p=prompt, view=target, url=target_url, item=worker:
            self._finish_ai(
                p, answer, False, view, url, item
            )
        )
        worker.failed.connect(
            lambda message, p=prompt, view=target, url=target_url, item=worker:
            self._finish_ai(
                p, message, True, view, url, item
            )
        )
        worker.progress.connect(
            lambda status, item=worker: self._show_ai_progress(item, status)
        )
        worker.finished.connect(
            lambda item=worker: self._ai_requests.discard(item)
        )
        worker.finished.connect(
            lambda item=worker: self._release_ai_busy(item)
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _finish_ai(
        self,
        prompt: str,
        answer: str,
        error: bool,
        target: BrowserView | None,
        target_url: str,
        worker: AIRequestThread,
    ) -> None:
        self._release_ai_busy(worker)
        if not self.preferences.bottom_ai:
            return
        if target:
            if (
                self.tabs.indexOf(target) < 0
                or target.url().toString() != target_url
            ):
                return
            target.page().runJavaScript(
                "(() => {"
                "const box=document.getElementById('bottom-ai-answer');"
                "const text=document.getElementById('bottom-ai-text');"
                "if(!box||!text)return;"
                f"box.dataset.error={json.dumps(str(error).lower())};"
                f"text.textContent={json.dumps(answer)};"
                "})();"
            )
            return
        self.sidebar.show_ai_answer(prompt, answer, error=error)

    def _release_ai_busy(self, worker: AIRequestThread) -> None:
        if self._ai_busy_owner is worker:
            self._ai_busy_owner = None
            self.sidebar.set_ai_busy(False)

    def _show_ai_progress(self, worker: AIRequestThread, status: str) -> None:
        if self._ai_busy_owner is worker:
            self.sidebar.set_ai_progress(status)

    def _show_settings(self) -> None:
        dialog = SettingsDialog(self.settings_store, self)
        dialog.setting_changed.connect(self._apply_setting)
        dialog.exec()

    def _apply_setting(self, key: str, enabled: bool) -> None:
        self.preferences = self.settings_store.set(key, enabled)
        self.sidebar.apply_preferences(self.preferences)
        if key == "block_ads":
            self.privacy_interceptor.set_ad_blocking(enabled)
        elif key == "block_trackers":
            self.privacy_interceptor.set_tracker_blocking(enabled)
        elif key == "bottom_ai" and not enabled:
            for worker in tuple(self._ai_requests):
                worker.requestInterruption()
            self._ai_busy_owner = None
            self.sidebar.set_ai_busy(False)
        elif key == "sidebar_open":
            self.sidebar.setVisible(enabled)
        elif key == "battery_saver":
            self._apply_battery_policy()
        self.statusBar().showMessage(
            f"{key.replace('_', ' ').title()} {'on' if enabled else 'off'}.",
            2200,
        )

    def _show_extensions(self) -> None:
        self.sidebar.show()
        self.ai_button.setProperty("active", True)
        self.statusBar().showMessage(
            "Privacy, extensions, and battery controls are open.",
            3000,
        )

    def go_back(self) -> None:
        browser = self.current_browser()
        if browser:
            browser.back()

    def go_forward(self) -> None:
        browser = self.current_browser()
        if browser:
            browser.forward()

    def reload_or_stop(self) -> None:
        browser = self.current_browser()
        if not browser:
            return
        if self._is_loading:
            browser.stop()
        else:
            browser.reload()

    def change_zoom(self, change: float) -> None:
        browser = self.current_browser()
        if browser:
            browser.setZoomFactor(
                min(3.0, max(0.25, browser.zoomFactor() + change))
            )

    def reset_zoom(self) -> None:
        browser = self.current_browser()
        if browser:
            browser.setZoomFactor(1.0)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _current_tab_changed(self, index: int) -> None:
        del index
        browser = self.current_browser()
        if not browser:
            return
        self.address_bar.setText(
            "" if browser.property("isNewTab") else display_url(browser.url().toString())
        )
        self._update_navigation_state()
        self._apply_battery_policy()
        self._update_privacy_status()
        title = browser.title() or "New Tab"
        self.setWindowTitle(f"{title} — {APP_NAME}")

    def _apply_battery_policy(self) -> None:
        """Freeze inactive Chromium pages without discarding their state."""
        for index in range(self.tabs.count()):
            view = self.tabs.widget(index)
            if not isinstance(view, BrowserView):
                continue
            state = QWebEnginePage.LifecycleState.Active
            if self.preferences.battery_saver and view is not self.current_browser():
                state = QWebEnginePage.LifecycleState.Frozen
            view.page().setLifecycleState(state)

    def _update_privacy_status(self) -> None:
        browser = self.current_browser()
        if not browser:
            return
        counts = self.privacy_interceptor.page_counts(browser.url().toString())
        self.sidebar.set_block_counts(counts.ads, counts.trackers)
        self.security_icon.setToolTip(
            f"Privacy protection · {counts.total} requests blocked on this page"
        )

    def _update_tab_title(self, browser: BrowserView, title: str) -> None:
        index = self.tabs.indexOf(browser)
        if index < 0:
            return
        clean_title = (
            "New Tab"
            if browser.property("isNewTab")
            else (" ".join(title.split()) or "New Tab")
        )
        self.tabs.setTabText(index, clean_title)
        self.tabs.setTabToolTip(index, clean_title)
        if browser is self.current_browser():
            self.setWindowTitle(f"{clean_title} — {APP_NAME}")

    def _update_tab_icon(self, browser: BrowserView, icon: QIcon) -> None:
        index = self.tabs.indexOf(browser)
        if index >= 0 and not icon.isNull():
            self.tabs.setTabIcon(index, icon)

    def _on_url_changed(self, browser: BrowserView, url: QUrl) -> None:
        browser.setProperty("aiRequestUrl", "")
        browser.setProperty(
            "isNewTab",
            url.scheme() == "bottom" and url.host().lower() == "newtab",
        )
        if browser is self.current_browser():
            value = "" if browser.property("isNewTab") else display_url(url.toString())
            self.address_bar.setText(value)
            self.address_bar.setCursorPosition(0)
            self._update_navigation_state()

    def _on_load_started(self, browser: BrowserView) -> None:
        browser.setProperty("aiRequestUrl", "")
        if browser is not self.current_browser():
            return
        self._is_loading = True
        self.reload_button.setIcon(make_icon("stop"))
        self.reload_button.setToolTip("Stop loading (Esc)")
        self.progress.setValue(2)
        self.progress.show()

    def _on_load_progress(self, browser: BrowserView, value: int) -> None:
        if browser is self.current_browser():
            self.progress.setValue(value)

    def _on_load_finished(self, browser: BrowserView, ok: bool) -> None:
        if ok and self.preferences.youtube_dislikes:
            browser.page().runJavaScript(youtube_dislike_injection_js())
        if ok and browser.url().scheme() == "bottom" and browser.url().host() == "search":
            query = parse_qs(browser.url().query()).get("q", [""])[0].strip()
            request_url = browser.url().toString()
            if (
                query
                and self.preferences.bottom_ai
                and browser.property("aiRequestUrl") != request_url
            ):
                browser.setProperty("aiRequestUrl", request_url)
                self._start_ai_request(
                    f"Answer this search query concisely: {query}",
                    self._search_context(query),
                    browser,
                    request_url,
                )
        if browser is self.current_browser():
            self._is_loading = False
            self.reload_button.setIcon(make_icon("refresh"))
            self.reload_button.setToolTip("Refresh (Ctrl+R)")
            self.progress.hide()
            self._update_navigation_state()
            self._update_privacy_status()
            if not ok and browser.url().toString() not in {"", "about:blank"}:
                self.statusBar().showMessage(
                    "This page could not be loaded.", 3500
                )

    def _update_navigation_state(self) -> None:
        browser = self.current_browser()
        self.back_button.setEnabled(bool(browser and browser.history().canGoBack()))
        self.forward_button.setEnabled(
            bool(browser and browser.history().canGoForward())
        )

    def _render_process_crashed(self, browser, status, code: int) -> None:
        del status
        index = self.tabs.indexOf(browser)
        if index >= 0:
            self.tabs.setTabText(index, "Page crashed")
        QMessageBox.warning(
            self,
            "Page stopped",
            f"The page process stopped unexpectedly (code {code}). "
            "Reload the tab to try again.",
        )

    def _handle_download(
        self, download: QWebEngineDownloadRequest
    ) -> None:
        default_name = download.downloadFileName() or "download"
        default_path = str(Path.home() / "Downloads" / default_name)
        destination, _ = QFileDialog.getSaveFileName(
            self, "Save download", default_path
        )
        if not destination:
            download.cancel()
            return

        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        download.setDownloadDirectory(str(target.parent))
        download.setDownloadFileName(target.name)
        download.accept()
        self._download_count += 1
        self.download_label.setText(f"Downloading {target.name}")
        self.download_label.show()
        download.stateChanged.connect(
            lambda state, item=download, name=target.name: self._download_state_changed(
                item, state, name
            )
        )

    def _download_state_changed(
        self,
        download: QWebEngineDownloadRequest,
        state: QWebEngineDownloadRequest.DownloadState,
        filename: str,
    ) -> None:
        finished_states = {
            QWebEngineDownloadRequest.DownloadState.DownloadCompleted,
            QWebEngineDownloadRequest.DownloadState.DownloadCancelled,
            QWebEngineDownloadRequest.DownloadState.DownloadInterrupted,
        }
        if state not in finished_states:
            return
        self._download_count = max(0, self._download_count - 1)
        if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            self.statusBar().showMessage(f"Downloaded {filename}", 5000)
        elif state == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted:
            self.statusBar().showMessage(
                f"Download failed: {download.interruptReasonString()}", 5000
            )
        if self._download_count == 0:
            self.download_label.hide()

    def _show_menu(self) -> None:
        menu = QMenu(self)
        menu.setObjectName("browserMenu")
        self._menu_action(menu, "New tab", "Ctrl+T", self.add_new_tab)
        reopen = self._menu_action(
            menu, "Reopen closed tab", "Ctrl+Shift+T", self.reopen_closed_tab
        )
        reopen.setEnabled(bool(self.closed_tabs))
        menu.addSeparator()
        stats = self.search_engine.stats()
        documents = int(stats.get("document_count", stats.get("documents", 0)))
        domains = int(stats.get("domain_count", stats.get("domains", 0)))
        index_status = self._menu_action(
            menu,
            f"Bottom Search · {documents:,} pages · {domains:,} sites",
            "",
            lambda: None,
        )
        index_status.setEnabled(False)
        refresh = self._menu_action(
            menu, "Refresh private search index", "", self.refresh_search_index
        )
        refresh.setEnabled(
            not bool(self.search_crawler and self.search_crawler.running)
        )
        self._menu_action(
            menu, "Clear search history", "", self.clear_search_history
        )
        self._menu_action(
            menu, "Reset starter index…", "", self.reset_search_index
        )
        menu.addSeparator()
        self._menu_action(
            menu, "Zoom in", "Ctrl++", lambda: self.change_zoom(0.1)
        )
        self._menu_action(
            menu, "Zoom out", "Ctrl+-", lambda: self.change_zoom(-0.1)
        )
        self._menu_action(menu, "Actual size", "Ctrl+0", self.reset_zoom)
        menu.addSeparator()
        self._menu_action(
            menu, "Full screen", "F11", self.toggle_fullscreen
        )
        self._menu_action(menu, "Quit", "Ctrl+Q", QApplication.quit)
        menu.exec(
            self.menu_button.mapToGlobal(
                self.menu_button.rect().topRight()
            )
        )

    def refresh_search_index(self) -> None:
        if not self.preferences.background_indexing:
            self.statusBar().showMessage(
                "Background index refresh is disabled in Settings.", 4000
            )
            return
        if self.search_crawler and self.search_crawler.running:
            self.statusBar().showMessage(
                "Bottom Search is already refreshing its index.", 3500
            )
            return
        self.search_crawler = self.search_engine.crawl(
            SEARCH_SEEDS, max_pages=60, same_host=True
        )
        self.download_label.setText("Bottom Search is learning…")
        self.download_label.show()
        self.crawl_timer.start()
        self.statusBar().showMessage(
            "Refreshing the private index in the background.", 4000
        )

    def _check_crawler(self) -> None:
        crawler = self.search_crawler
        if crawler is None or crawler.running:
            return
        self.crawl_timer.stop()
        self.download_label.hide()
        message = f"Bottom Search indexed {crawler.indexed} new pages."
        if crawler.errors:
            message += f" {len(crawler.errors)} pages could not be read."
        self.statusBar().showMessage(message, 6000)
        browser = self.current_browser()
        if browser and browser.url().scheme() == "bottom":
            browser.reload()

    def clear_search_history(self) -> None:
        self.search_engine.clear_history()
        self.statusBar().showMessage(
            "Private Bottom Search history cleared.", 4000
        )

    def reset_search_index(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset Bottom Search?",
            "This removes pages learned by the crawler and restores the "
            "built-in starter knowledge. Your normal browser history is unchanged.",
            QMessageBox.StandardButton.Reset
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Reset:
            return
        if self.search_crawler and self.search_crawler.running:
            self.search_crawler.stop()
            self.search_crawler.join()
        self.search_engine.clear_index()
        self.search_engine.seed_starter_corpus(
            resource_path("starter_corpus.json")
        )
        browser = self.current_browser()
        if browser and browser.url().scheme() == "bottom":
            browser.reload()
        self.statusBar().showMessage("Starter search index restored.", 4000)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self.search_crawler and self.search_crawler.running:
            self.search_crawler.stop()
            self.search_crawler.join()
        for worker in tuple(self._ai_requests):
            worker.requestInterruption()
        if any(
            worker.isRunning() and not worker.wait(10_000)
            for worker in tuple(self._ai_requests)
        ):
            self.statusBar().showMessage(
                "Finishing private AI shutdown safely…"
            )
            event.ignore()
            QTimer.singleShot(500, self.close)
            return
        self.search_engine.close()
        super().closeEvent(event)

    @staticmethod
    def _menu_action(
        menu: QMenu, text: str, shortcut: str, callback
    ) -> QAction:
        action = QAction(text, menu)
        action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    @staticmethod
    def _stylesheet() -> str:
        return """
        QMainWindow, QWidget {
            color: #edf0f8;
            background: #0d0e18;
            font-family: "Avenir Next", "Segoe UI", sans-serif;
            font-size: 13px;
        }
        QTabWidget::pane {
            border: none;
            background: #0b0d13;
        }
        QTabBar {
            background: #121322;
            border-top: 1px solid #2b2b49;
        }
        QTabBar::tab {
            min-height: 38px;
            max-height: 38px;
            margin: 5px 2px 5px 2px;
            padding: 0 12px;
            color: #8b91ad;
            background: #18192a;
            border: 1px solid #292a45;
            border-radius: 9px;
        }
        QTabBar::tab:first {
            margin-left: 8px;
        }
        QTabBar::tab:selected {
            color: #fbfaff;
            background: #302855;
            border-color: #7969d9;
        }
        QTabBar::tab:hover:!selected {
            color: #d6d2ee;
            background: #24233c;
        }
        QTabBar::close-button {
            subcontrol-position: right;
            border-radius: 5px;
        }
        QTabBar::close-button:hover {
            background: #49406e;
        }
        #newTabButton {
            min-width: 36px;
            max-width: 36px;
            min-height: 36px;
            max-height: 36px;
            margin: 6px 9px 6px 4px;
            background: transparent;
            border: none;
            border-radius: 9px;
        }
        #newTabButton:hover {
            background: #292447;
        }
        #bottomBar {
            background: #141526;
            border-top: 1px solid #302d52;
        }
        QPushButton[navButton="true"] {
            background: transparent;
            border: none;
            border-radius: 11px;
        }
        QPushButton[navButton="true"]:hover {
            background: #252344;
        }
        QPushButton[navButton="true"]:pressed {
            background: #3c3563;
        }
        QPushButton[navButton="true"]:disabled {
            opacity: .32;
        }
        #addressFrame {
            background: #202039;
            border: 1px solid #39365e;
            border-radius: 13px;
        }
        #addressFrame:focus-within {
            border-color: #55d7e8;
        }
        #addressBar {
            color: #f5f3ff;
            selection-color: white;
            selection-background-color: #6656c9;
            background: transparent;
            border: none;
            padding: 0;
            font-size: 14px;
        }
        #addressBar::placeholder {
            color: #858aa6;
        }
        #loadProgress {
            background: #17182a;
            border: none;
        }
        #loadProgress::chunk {
            background: qlineargradient(
                x1:0, y1:0, x2:1, y2:0,
                stop:0 #8a6dff, stop:1 #4ed8df
            );
        }
        #downloadLabel {
            padding: 7px 10px;
            color: #aeb0ca;
            background: #22223b;
            border: 1px solid #39365e;
            border-radius: 9px;
        }
        QMenu {
            padding: 7px;
            background: #1c1c31;
            border: 1px solid #3b3860;
            border-radius: 11px;
        }
        QMenu::item {
            min-width: 220px;
            padding: 9px 28px 9px 12px;
            border-radius: 7px;
        }
        QMenu::item:selected {
            background: #332e55;
        }
        QMenu::separator {
            height: 1px;
            margin: 6px 8px;
            background: #3e3a62;
        }
        QToolTip {
            color: #e9edf7;
            background: #202531;
            border: 1px solid #343b4d;
            padding: 5px 7px;
        }
        QStatusBar {
            color: #9da2bf;
            background: #141526;
            border-top: 1px solid #302d52;
        }
        #accentControl, #controlButton, #closeTabButton {
            min-height: 38px;
            padding: 0 13px;
            border-radius: 10px;
            font-weight: 700;
        }
        #accentControl {
            color: #101221;
            background: #55d7e8;
            border: 1px solid #6ae4ee;
        }
        #accentControl:hover { background: #72e8ee; }
        #accentControl[active="true"] {
            color: #faf8ff;
            background: #7969d9;
            border-color: #988aff;
        }
        #controlButton {
            color: #c9c5eb;
            background: #262443;
            border: 1px solid #423b6a;
        }
        #controlButton:hover { background: #35305a; border-color: #6859bb; }
        #closeTabButton {
            color: #d7a6c6;
            background: #2a202f;
            border: 1px solid #59394f;
        }
        #closeTabButton:hover { color: #ffd9e8; background: #3b263c; }
        #browserSidebar {
            background: #17172a;
            border-left: 1px solid #37345a;
        }
        #eyebrow, #sectionLabel {
            color: #7ecbd6;
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 1.6px;
        }
        #sidebarClose {
            color: #a8a5c5;
            background: transparent;
            border: none;
            font-size: 21px;
        }
        #sidebarClose:hover { color: #ffffff; }
        #aiHero {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #30265a, stop:1 #1d3b50);
            border: 1px solid #5e5aa1;
            border-radius: 15px;
        }
        #heroTitle { color: #ffffff; font-size: 21px; font-weight: 800; }
        #secondaryText { color: #aaa9c6; line-height: 1.4; }
        #primaryAction {
            color: #171526; background: #6ee2e8; border: none;
            border-radius: 9px; padding: 10px; font-weight: 750;
        }
        #primaryAction:hover { background: #9af0f0; }
        #sidebarCard {
            background: #1d1d33; border: 1px solid #353455; border-radius: 13px;
        }
        #quietAction, #footerAction {
            color: #c7c3e1; background: transparent; border: none;
            text-align: left; padding: 7px 4px; border-radius: 6px;
        }
        #quietAction:hover, #footerAction:hover { color: #ffffff; background: #292747; }
        QCheckBox { color: #c8c7dc; spacing: 8px; padding: 3px 0; }
        QCheckBox::indicator {
            width: 30px; height: 18px; border-radius: 9px;
            background: #373650; border: 1px solid #4b4870;
        }
        QCheckBox::indicator:checked { background: #55d7e8; border-color: #55d7e8; }
        QCheckBox::indicator:unchecked { image: none; }
        #settingsDialog { background: #18182b; }
        #dialogTitle { color: #f5f3ff; font-size: 22px; font-weight: 800; }
        """


def main() -> int:
    if "--ai-smoke-test" in sys.argv:
        app = QCoreApplication(sys.argv[:1])
        app.setApplicationName(APP_NAME)
        app.setApplicationVersion(APP_VERSION)
        app.setOrganizationName("Bottom Browser")
        answers: list[str] = []
        errors: list[str] = []
        worker = AIRequestThread(
            "Reply briefly to confirm that private local inference is running."
        )
        worker.progress.connect(lambda status: print(status, flush=True))
        worker.completed.connect(answers.append)
        worker.failed.connect(errors.append)
        worker.run()
        if errors or not answers:
            print(errors[0] if errors else "Local AI returned no answer.", file=sys.stderr)
            return 1
        print(f"BOTTOM_AI_SMOKE_OK: {answers[0]}", flush=True)
        return 0
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--enable-features=VaapiVideoDecoder")
    register_bottom_scheme()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Bottom Browser")
    app.setWindowIcon(application_icon())
    window = BrowserWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())