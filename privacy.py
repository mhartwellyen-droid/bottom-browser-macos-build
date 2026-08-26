"""Local, opt-in privacy tools for Bottom Browser.

The request interceptor deliberately has no subscription-list or network
dependency.  Its small ruleset is intended as a conservative baseline, not a
replacement for a maintained content-blocking list.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from threading import RLock
from typing import Any, Literal
from urllib.parse import urlsplit

try:  # Keeping the decision engine importable also makes it easy to unit test.
    from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInterceptor
except ImportError:  # pragma: no cover - used only in non-Qt tooling.
    class QWebEngineUrlRequestInterceptor:  # type: ignore[no-redef]
        """Minimal compatibility base when PyQt6-WebEngine is unavailable."""

        def __init__(self, parent: object | None = None) -> None:
            del parent


BlockKind = Literal["ad", "tracker"]

# These are intentionally explicit, stable rules rather than a remotely fetched
# list.  A suffix rule includes subdomains but never matches lookalike domains.
AD_DOMAINS = frozenset(
    {
        "doubleclick.net",
        "googlesyndication.com",
        "googleadservices.com",
        "adnxs.com",
        "adsrvr.org",
        "amazon-adsystem.com",
        "taboola.com",
        "outbrain.com",
        "criteo.com",
        "creativecdn.com",
        "rubiconproject.com",
        "openx.net",
        "pubmatic.com",
    }
)
TRACKER_DOMAINS = frozenset(
    {
        "google-analytics.com",
        "googletagmanager.com",
        "analytics.google.com",
        "segment.io",
        "segment.com",
        "mixpanel.com",
        "hotjar.com",
        "mouseflow.com",
        "fullstory.com",
        "amplitude.com",
        "scorecardresearch.com",
        "quantserve.com",
        "facebook.net",
        "connect.facebook.net",
        "bat.bing.com",
    }
)

# Path rules are applied only to web requests. They cover common first-party
# ad/analytics endpoints without treating ordinary content URLs as trackers.
AD_PATH_RULES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (r"/ads?(?:[/?._-]|$)", r"/adserver(?:[/?._-]|$)", r"/banner[s]?(?:[/?._-]|$)")
)
TRACKER_PATH_RULES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"/(?:analytics|metrics|telemetry)(?:[/?._-]|$)",
        r"/collect(?:[/?._-]|$)",
        r"/pixel(?:[/?._-]|$)",
        r"/track(?:[/?._-]|$)",
    )
)


@dataclass(frozen=True, slots=True)
class PrivacySettings:
    """Runtime settings snapshot for :class:`PrivacyRequestInterceptor`."""

    block_ads: bool = True
    block_trackers: bool = True


@dataclass(frozen=True, slots=True)
class BlockCounts:
    """A count snapshot. ``total`` is always ``ads + trackers``."""

    ads: int = 0
    trackers: int = 0

    @property
    def total(self) -> int:
        return self.ads + self.trackers


def _host_matches(host: str, domains: frozenset[str]) -> bool:
    host = host.rstrip(".").lower()
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _request_text(value: Any) -> str:
    """Extract a URL string from either QUrl or a test-double URL."""

    if hasattr(value, "toString"):
        return str(value.toString())
    return str(value)


def _page_key(url: str) -> str:
    """Return a stable page key without fragments or credentials."""

    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme.lower()}://{host}{port}{parsed.path}?{parsed.query}"


class PrivacyRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """A QWebEngine-compatible, thread-safe local ad/tracker interceptor.

    Install one instance on a ``QWebEngineProfile`` with
    ``profile.setUrlRequestInterceptor(interceptor)``.  Counters are keyed by
    the request's first-party URL, so callers can display per-tab numbers by
    passing the current page URL to :meth:`page_counts`.
    """

    def __init__(
        self,
        parent: object | None = None,
        *,
        block_ads: bool = True,
        block_trackers: bool = True,
    ) -> None:
        super().__init__(parent)
        self._lock = RLock()
        self._settings = PrivacySettings(block_ads, block_trackers)
        self._total = BlockCounts()
        self._pages: defaultdict[str, BlockCounts] = defaultdict(BlockCounts)

    def settings(self) -> PrivacySettings:
        """Return an immutable snapshot of current toggle values."""
        with self._lock:
            return self._settings

    def update_settings(
        self, *, block_ads: bool | None = None, block_trackers: bool | None = None
    ) -> PrivacySettings:
        """Atomically update either independent blocking toggle."""
        with self._lock:
            self._settings = PrivacySettings(
                self._settings.block_ads if block_ads is None else bool(block_ads),
                self._settings.block_trackers
                if block_trackers is None
                else bool(block_trackers),
            )
            return self._settings

    def set_ad_blocking(self, enabled: bool) -> PrivacySettings:
        return self.update_settings(block_ads=enabled)

    def set_tracker_blocking(self, enabled: bool) -> PrivacySettings:
        return self.update_settings(block_trackers=enabled)

    def classify(self, url: str) -> BlockKind | None:
        """Classify a URL using bundled domain and path rules, without blocking."""
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        host, path = (parsed.hostname or "").lower(), parsed.path or "/"
        if _host_matches(host, AD_DOMAINS) or any(rule.search(path) for rule in AD_PATH_RULES):
            return "ad"
        if _host_matches(host, TRACKER_DOMAINS) or any(
            rule.search(path) for rule in TRACKER_PATH_RULES
        ):
            return "tracker"
        return None

    def should_block(self, url: str) -> BlockKind | None:
        """Return the enabled matching kind, or ``None`` when it should load."""
        kind = self.classify(url)
        with self._lock:
            if kind == "ad" and self._settings.block_ads:
                return kind
            if kind == "tracker" and self._settings.block_trackers:
                return kind
        return None

    def interceptRequest(self, info: Any) -> None:  # noqa: N802 - Qt API name
        """Block a matching QWebEngine request and account for it safely."""
        url = _request_text(info.requestUrl())
        kind = self.should_block(url)
        if kind is None:
            return
        info.block(True)
        first_party = _request_text(info.firstPartyUrl())
        self._record(_page_key(first_party), kind)

    def _record(self, page: str, kind: BlockKind) -> None:
        with self._lock:
            def increment(value: BlockCounts) -> BlockCounts:
                return BlockCounts(
                    ads=value.ads + (kind == "ad"),
                    trackers=value.trackers + (kind == "tracker"),
                )

            self._total = increment(self._total)
            self._pages[page] = increment(self._pages[page])

    def total_counts(self) -> BlockCounts:
        with self._lock:
            return self._total

    def page_counts(self, page_url: str) -> BlockCounts:
        with self._lock:
            return self._pages.get(_page_key(page_url), BlockCounts())

    def reset_counts(self, page_url: str | None = None) -> None:
        """Reset all counters, or only the first-party page identified by URL."""
        with self._lock:
            if page_url is None:
                self._total = BlockCounts()
                self._pages.clear()
            else:
                self._pages.pop(_page_key(page_url), None)


def youtube_dislike_injection_js() -> str:
    """Return self-contained, failure-tolerant JS for YouTube watch pages.

    The script validates the host and video id *before* issuing its only
    request. It uses Return YouTube Dislike's documented public votes endpoint,
    renders response text with ``textContent``, and silently leaves YouTube
    untouched when markup or the service is unavailable.
    """
    return r"""(() => {
  "use strict";
  try {
    const url = new URL(window.location.href);
    const host = url.hostname.toLowerCase();
    if ((host !== "youtube.com" && host !== "www.youtube.com") ||
        url.pathname !== "/watch") return;
    const videoId = url.searchParams.get("v");
    if (!videoId || !/^[A-Za-z0-9_-]{6,}$/.test(videoId)) return;
    const marker = "bottom-r yd-dislike-count";
    const render = (data) => {
      if (document.querySelector("." + marker.split(" ")[0])) return;
      const target = document.querySelector(
        "#top-level-buttons-computed ytd-menu-renderer, #actions #menu"
      );
      if (!target || !Number.isFinite(Number(data && data.dislikes))) return;
      const item = document.createElement("span");
      item.className = marker;
      item.textContent = `${Number(data.dislikes).toLocaleString()} dislikes · Return YouTube Dislike`;
      item.style.cssText = "margin-left:8px;font-size:12px;opacity:.75;white-space:nowrap";
      item.title = "Dislike count provided by Return YouTube Dislike";
      target.appendChild(item);
    };
    fetch("https://returnyoutubedislikeapi.com/votes?videoId=" +
          encodeURIComponent(videoId), { credentials: "omit" })
      .then((response) => response.ok ? response.json() : null)
      .then(render)
      .catch(() => {});
  } catch (_) {}
})();"""


# Short alias convenient for a page.loadFinished connection.
youtube_dislike_script = youtube_dislike_injection_js
