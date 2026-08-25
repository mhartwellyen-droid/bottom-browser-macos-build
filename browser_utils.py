"""Address-bar helpers for URLs and the private Bottom Search scheme."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qs, quote_plus, urlsplit, urlunsplit


SEARCH_URL = "bottom://search/?q={query}"
ALLOWED_SCHEMES = {"http", "https", "file", "ftp", "about"}
DOMAIN_PATTERN = re.compile(
    r"^(?:localhost|(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})"
    r"(?::\d{1,5})?(?:[/?#].*)?$",
    re.IGNORECASE,
)


def normalize_user_input(text: str) -> str:
    """Turn address-bar input into a safe URL or local Bottom Search query."""
    value = text.strip()
    if not value:
        return ""

    parsed = urlsplit(value)
    if parsed.scheme.lower() in ALLOWED_SCHEMES:
        return value

    # Do not execute script/custom schemes typed into the omnibox.
    if parsed.scheme:
        return SEARCH_URL.format(query=quote_plus(value))

    host_candidate = value.split("/", 1)[0].split(":", 1)[0]
    is_ip = False
    try:
        ipaddress.ip_address(host_candidate.strip("[]"))
        is_ip = True
    except ValueError:
        pass

    if DOMAIN_PATTERN.match(value) or is_ip:
        scheme = "http" if value.lower().startswith("localhost") else "https"
        return f"{scheme}://{value}"

    return SEARCH_URL.format(query=quote_plus(value))


def display_url(url: str) -> str:
    """Present a readable URL while preserving the full path and query."""
    if not url or url == "about:blank":
        return ""
    parsed = urlsplit(url)
    if parsed.scheme == "bottom":
        if parsed.netloc == "search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            return query
        return ""
    if parsed.scheme in {"http", "https"}:
        return urlunsplit(
            ("", parsed.netloc, parsed.path, parsed.query, parsed.fragment)
        ).lstrip("//")
    return url