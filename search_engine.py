"""Local, private full-text search for Bottom Browser.

Public API: :class:`LocalSearchEngine` stores documents in a SQLite database.
Use ``upsert_document(url, title, text, ...)``, ``search(query)``,
``seed_starter_corpus()``, ``history()``, ``clear_history()``, ``clear_index()``,
and ``stats()``.  ``crawl(urls, ...)`` starts a daemon background crawl and
returns a :class:`Crawler`; it deliberately never performs network work on the
caller's/UI thread.  All functionality uses the Python standard library.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import math
import re
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.error import HTTPError
from urllib.parse import urljoin, urldefrag, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

USER_AGENT = "BottomSearchBot/1.0 (+local Bottom Browser search)"
MAX_PAGE_BYTES = 1_500_000
MAX_TEXT_CHARS = 250_000
BAD_EXTENSIONS = frozenset((".pdf", ".zip", ".gz", ".tar", ".jpg", ".jpeg", ".png",
    ".gif", ".webp", ".svg", ".mp3", ".mp4", ".avi", ".mov", ".exe", ".dmg",
    ".iso", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".woff", ".ttf"))
TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)
# Query-only expansions.  These are deliberately small, transparent groups:
# they improve common vocabulary mismatches without changing stored documents
# or accepting user supplied FTS operators.
QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "heart": ("cardiac", "cardiovascular"),
    "cardiac": ("heart", "cardiovascular"),
    "weather": ("meteorology", "climate"),
    "meteorology": ("weather",),
    "python": ("py",),
    "list": ("lists", "array", "sequence"),
}

__all__ = ["Crawler", "LocalSearchEngine", "SearchResult", "USER_AGENT", "normalize_url"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_url(url: str) -> str:
    """Return a stable HTTP(S) URL without a fragment, or raise ValueError."""
    p = urlsplit(url.strip())
    if p.scheme.lower() not in ("http", "https") or not p.netloc:
        raise ValueError("only absolute http(s) URLs can be indexed")
    host = p.hostname.lower() if p.hostname else ""
    netloc = host + (f":{p.port}" if p.port else "")
    path = p.path or "/"
    return urlunsplit((p.scheme.lower(), netloc, path, p.query, ""))


def _domain(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title, self.description, self.canonical = "", "", ""
        self.noindex = False
        self.text: list[str] = []
        self.links: list[str] = []
        self._skip = 0
    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        a = dict(attrs)
        if tag in ("script", "style", "noscript", "template"): self._skip += 1
        if tag == "a" and a.get("href"): self.links.append(a["href"])
        if tag == "link" and (a.get("rel") or "").lower() == "canonical": self.canonical = a.get("href") or ""
        if tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            value = a.get("content") or ""
            if key in ("description", "og:description") and not self.description: self.description = value
            if key == "robots" and "noindex" in value.lower(): self.noindex = True
    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "template") and self._skip: self._skip -= 1
    def handle_data(self, data: str) -> None:
        if self._skip: return
        if self.lasttag == "title": self.title += data
        self.text.append(data)


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    score: float
    domain: str
    description: str = ""
    source: str = ""
    license: str = ""
    updated_at: str = ""


class LocalSearchEngine:
    """SQLite FTS5 index. Instances are safe to call from a crawler thread."""
    def __init__(self, database: str | Path, starter_corpus: str | Path | None = None) -> None:
        self.database = str(database)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.database, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._create_schema()
        if starter_corpus is not None: self.seed_starter_corpus(starter_corpus)

    def _create_schema(self) -> None:
        with self._db:
            self._db.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
              id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, domain TEXT NOT NULL,
              source TEXT NOT NULL DEFAULT '', license TEXT NOT NULL DEFAULT '',
              indexed_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(
              title, description, text, content='documents', content_rowid='id',
              tokenize='unicode61');
            CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
              INSERT INTO document_fts(rowid,title,description,text)
              VALUES (new.id,new.title,new.description,new.text); END;
            CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
              INSERT INTO document_fts(document_fts,rowid,title,description,text)
              VALUES ('delete',old.id,old.title,old.description,old.text); END;
            CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
              INSERT INTO document_fts(document_fts,rowid,title,description,text)
              VALUES ('delete',old.id,old.title,old.description,old.text);
              INSERT INTO document_fts(rowid,title,description,text)
              VALUES (new.id,new.title,new.description,new.text); END;
            CREATE TABLE IF NOT EXISTS query_history (
              id INTEGER PRIMARY KEY, query TEXT NOT NULL, searched_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS history_time ON query_history(searched_at DESC);
            CREATE TABLE IF NOT EXISTS metadata (
              key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    def close(self) -> None:
        with self._lock: self._db.close()

    def upsert_document(self, url: str, title: str, text: str, *, description: str = "",
                        source: str = "", license: str = "", updated_at: str | None = None) -> None:
        """Add or replace one document. URL uniqueness provides deduplication."""
        url = normalize_url(url)
        title = " ".join((title or url).split())[:1000]
        text = " ".join((text or "").split())[:MAX_TEXT_CHARS]
        if not text: raise ValueError("document text must not be empty")
        now = updated_at or _utcnow()
        with self._lock, self._db:
            self._db.execute("""INSERT INTO documents
              (url,title,description,text,domain,source,license,indexed_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?)
              ON CONFLICT(url) DO UPDATE SET title=excluded.title,description=excluded.description,
              text=excluded.text,domain=excluded.domain,
              source=CASE WHEN excluded.source='' THEN documents.source ELSE excluded.source END,
              license=CASE WHEN excluded.license='' THEN documents.license ELSE excluded.license END,
              updated_at=excluded.updated_at""",
              (url, title, " ".join(description.split())[:2000], text, _domain(url),
               source, license, now, now))

    def seed_starter_corpus(self, path: str | Path | None = None) -> int:
        """Idempotently import bundled JSON corpus; return documents processed."""
        path = Path(path) if path else Path(__file__).with_name("starter_corpus.json")
        payload = path.read_bytes()
        fingerprint = hashlib.sha256(payload).hexdigest()
        with self._lock:
            saved = self._db.execute(
                "SELECT value FROM metadata WHERE key='starter_corpus_sha256'"
            ).fetchone()
            if saved and saved[0] == fingerprint:
                return 0
        entries = json.loads(payload)
        if not isinstance(entries, list): raise ValueError("starter corpus must be a JSON list")
        now = _utcnow()
        rows = []
        for item in entries:
            url = normalize_url(item["url"])
            title = " ".join(str(item["title"]).split())[:1000]
            text = " ".join(str(item["text"]).split())[:MAX_TEXT_CHARS]
            if not title or not text:
                continue
            rows.append((
                url, title,
                " ".join(str(item.get("description", "")).split())[:2000],
                text, _domain(url), str(item.get("source", "")),
                str(item.get("license", "")), now, now,
            ))
        with self._lock, self._db:
            self._db.executemany("""INSERT INTO documents
              (url,title,description,text,domain,source,license,indexed_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?)
              ON CONFLICT(url) DO UPDATE SET title=excluded.title,
              description=excluded.description,text=excluded.text,
              domain=excluded.domain,source=excluded.source,
              license=excluded.license,updated_at=excluded.updated_at""", rows)
            self._db.execute(
                """INSERT INTO metadata(key,value) VALUES('starter_corpus_sha256',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (fingerprint,),
            )
        return len(entries)

    def search(self, query: str, limit: int = 10, *, record_history: bool = True) -> list[SearchResult]:
        """Search with BM25, freshness and diversity.

        All term groups (including a small set of query aliases) are required
        first. If that produces no documents, terms of three or more
        characters may use a safe prefix fallback for type-ahead discovery.
        In particular, ``hi`` never becomes ``hi*`` and therefore cannot
        accidentally find ``History``. Only tokens extracted by
        :data:`TOKEN_RE` enter FTS syntax, so user input cannot alter the FTS
        query grammar.
        """
        terms = TOKEN_RE.findall(query.lower())
        if not terms: return []
        limit = max(1, min(int(limit), 100))
        phrase = " ".join(terms)
        with self._lock:
            if record_history:
                with self._db: self._db.execute("INSERT INTO query_history(query,searched_at) VALUES (?,?)", (query.strip()[:500], _utcnow()))
            exact_match = self._expanded_match(terms)
            rows = self._fts_rows(exact_match, limit)
            exact_title_rows = self._db.execute(
                """SELECT d.*, 0.0 AS rank FROM documents d
                WHERE lower(d.title)=? LIMIT ?""",
                (phrase, limit),
            ).fetchall()
            if exact_title_rows:
                exact_ids = {row["id"] for row in exact_title_rows}
                rows = exact_title_rows + [
                    row for row in rows if row["id"] not in exact_ids
                ]
            prefix_terms = [term for term in terms if len(term) >= 3]
            if not rows and prefix_terms:
                # Prefix terms intentionally improve first-run discovery (for
                # example, "astron phy" can find astronomy and physics).
                fallback = " OR ".join(f'"{term}"*' for term in prefix_terms)
                rows = self._fts_rows(fallback, limit)
        seen: dict[str, int] = {}
        results = []
        now = datetime.now(timezone.utc)
        for row in rows:
            age = 3650.0
            try: age = max(0, (now - datetime.fromisoformat(row["updated_at"])).days)
            except ValueError: pass
            count = seen.get(row["domain"], 0); seen[row["domain"]] = count + 1
            # FTS bm25 is negative (more negative is better).  Exact titles
            # and title phrases are intentional-navigation signals and should
            # dominate broad corpus matches.
            title = " ".join(TOKEN_RE.findall(row["title"].lower()))
            title_bonus = 0.0
            if title == phrase:
                title_bonus = 1000.0
            elif phrase and phrase in title:
                title_bonus = 100.0
            elif phrase and phrase in row["description"].lower():
                title_bonus = 15.0
            score = -float(row["rank"]) + title_bonus + .25 / (1 + age / 30) - .18 * count
            results.append(SearchResult(row["url"], row["title"], self._snippet(row["text"], terms),
                                        score, row["domain"], row["description"], row["source"],
                                        row["license"], row["updated_at"]))
        return sorted(results, key=lambda r: r.score, reverse=True)[:limit]

    @staticmethod
    def _expanded_match(terms: list[str]) -> str:
        """Build an AND query from fixed, safely quoted token alternatives."""
        groups = []
        for term in terms:
            alternatives = (term,) + QUERY_ALIASES.get(term, ())
            # Alternatives are module constants or TOKEN_RE tokens, but quote
            # each one anyway so this remains a closed FTS grammar.
            groups.append("(" + " OR ".join(f'"{value}"' for value in alternatives) + ")")
        return " AND ".join(groups)

    def _fts_rows(self, match: str, limit: int) -> list[sqlite3.Row]:
        return self._db.execute("""SELECT d.*, bm25(document_fts, 8.0, 3.0, 1.0) AS rank
          FROM document_fts JOIN documents d ON d.id=document_fts.rowid
          WHERE document_fts MATCH ? ORDER BY rank LIMIT ?""", (match, limit * 5)).fetchall()

    @staticmethod
    def _snippet(text: str, terms: list[str], width: int = 260) -> str:
        """Produce escaped HTML with only query matches wrapped in ``<mark>``."""
        match = re.search("|".join(re.escape(t) for t in terms), text, re.I) if terms else None
        start = max(0, (match.start() if match else 0) - width // 3)
        excerpt = text[start:start + width].strip()
        if start: excerpt = "… " + excerpt
        if start + width < len(text): excerpt += " …"
        safe = html.escape(excerpt)
        return re.sub("(" + "|".join(re.escape(html.escape(t)) for t in terms) + ")",
                      r"<mark>\1</mark>", safe, flags=re.I)

    def history(self, limit: int = 50) -> list[dict[str, str]]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT query,searched_at FROM query_history ORDER BY id DESC LIMIT ?", (max(1, limit),))]
    def clear_history(self) -> None:
        with self._lock, self._db: self._db.execute("DELETE FROM query_history")
    def clear_index(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM documents")
            self._db.execute(
                "DELETE FROM metadata WHERE key='starter_corpus_sha256'"
            )
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"documents": self._db.execute("SELECT count(*) FROM documents").fetchone()[0],
                    "history": self._db.execute("SELECT count(*) FROM query_history").fetchone()[0],
                    "domains": self._db.execute("SELECT count(DISTINCT domain) FROM documents").fetchone()[0]}
    def crawl(self, urls: Iterable[str], *, max_pages: int = 20, same_host: bool = False) -> "Crawler":
        crawler = Crawler(self, max_pages=max_pages, same_host=same_host)
        crawler.start(urls)
        return crawler


class Crawler:
    """Small polite crawler; call ``start`` to launch it and ``join`` to await it."""
    def __init__(self, engine: LocalSearchEngine, *, max_pages: int = 20, same_host: bool = False,
                 timeout: float = 5.0) -> None:
        self.engine, self.max_pages, self.same_host, self.timeout = engine, max(1, max_pages), same_host, timeout
        self.max_requests = self.max_pages * 4
        self.max_duration = max(30.0, min(self.max_pages * 5.0, 300.0))
        self.requests = 0
        self.indexed = self.skipped = 0
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request: dict[str, float] = {}
        self._seed_hosts: set[str] = set()
        self._deadline = 0.0
        self._opener = build_opener(_SafeRedirectHandler(self._safe_target))
    def start(self, urls: Iterable[str]) -> "Crawler":
        initial = list(urls)
        self._seed_hosts = {
            _domain(url) for url in initial
            if urlsplit(url).scheme.lower() in ("http", "https")
        }
        self._deadline = time.monotonic() + self.max_duration
        self._thread = threading.Thread(target=self._run, args=(initial,), daemon=True, name="BottomSearchCrawler")
        self._thread.start(); return self
    def join(self, timeout: float | None = None) -> bool:
        if self._thread: self._thread.join(timeout)
        return not self.running
    @property
    def running(self) -> bool: return bool(self._thread and self._thread.is_alive())
    def stop(self) -> None: self._stop.set()

    def _within_scope(self, url: str) -> bool:
        try:
            normalized = normalize_url(url)
        except ValueError:
            return False
        host = _domain(normalized)
        return bool(host) and (not self.same_host or host in self._seed_hosts)

    def _safe_target(self, url: str) -> bool:
        """Allow only scoped public-network HTTP(S) destinations."""
        if not self._within_scope(url):
            return False
        parsed = urlsplit(url)
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(
                    parsed.hostname,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            }
        except (OSError, ValueError):
            return False
        if not addresses:
            return False
        return all(ipaddress.ip_address(address).is_global for address in addresses)

    def _open(self, request: Request):
        if self._stop.is_set():
            raise RuntimeError("crawl cancelled")
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("crawl time budget exhausted")
        if self.requests >= self.max_requests:
            raise RuntimeError("crawl request budget exhausted")
        if not self._safe_target(request.full_url):
            raise ValueError("blocked non-public or out-of-scope URL")
        self.requests += 1
        response = self._opener.open(
            request,
            timeout=max(0.1, min(self.timeout, remaining)),
        )
        try:
            peer = response.fp.raw._sock.getpeername()[0]
            if not ipaddress.ip_address(peer).is_global:
                raise ValueError("connected peer is not on the public network")
        except (AttributeError, OSError, ValueError):
            response.close()
            raise
        return response

    def _read_limited(self, response, limit: int) -> bytes:
        """Read with byte, cancellation, socket, and wall-clock bounds."""
        chunks: list[bytes] = []
        total = 0
        sock = response.fp.raw._sock
        read_deadline = min(
            self._deadline,
            time.monotonic() + self.timeout,
        )
        while total <= limit:
            if self._stop.is_set():
                raise RuntimeError("crawl cancelled")
            remaining = read_deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("response read time budget exhausted")
            sock.settimeout(max(0.1, min(1.0, remaining)))
            try:
                chunk = response.read1(min(64 * 1024, limit + 1 - total))
            except (TimeoutError, socket.timeout):
                continue
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)

    def _allowed(self, url: str) -> tuple[bool, float]:
        p = urlsplit(url); host = p.netloc
        if not self._safe_target(url) or p.path.lower().endswith(tuple(BAD_EXTENSIONS)): return False, 0
        rp = self._robots.get(host)
        if rp is None:
            rp = RobotFileParser(); rp.set_url(f"{p.scheme}://{host}/robots.txt")
            try:
                # RobotFileParser.read cannot set a user agent. Fetch explicitly
                # so every HTTP request identifies this crawler consistently.
                request = Request(rp.url, headers={"User-Agent": USER_AGENT})
                with self._open(request) as response:
                    rp.parse(self._read_limited(response, 256_000).decode(
                        response.headers.get_content_charset() or "utf-8", "replace"
                    ).splitlines())
            except HTTPError as exc:
                if exc.code in (404, 410):
                    rp.parse(["User-agent: *", "Allow: /"])
                else:
                    rp.parse(["User-agent: *", "Disallow: /"])
            except Exception:
                # Fail closed when a published policy cannot be checked.
                rp.parse(["User-agent: *", "Disallow: /"])
            self._robots[host] = rp
            self._last_request[host] = time.monotonic()
        return rp.can_fetch(USER_AGENT, url), (rp.crawl_delay(USER_AGENT) or rp.crawl_delay("*") or 1.0)

    def _fetch(self, url: str, delay: float) -> tuple[str, str, bool] | None:
        host = urlsplit(url).netloc; wait = delay - (time.monotonic() - self._last_request.get(host, 0))
        if wait > 0 and self._stop.wait(wait):
            return None
        self._last_request[host] = time.monotonic()
        req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
        with self._open(req) as response:
            final_url = response.geturl()
            if not self._safe_target(final_url):
                raise ValueError("redirect escaped crawler scope")
            if "html" not in response.headers.get_content_type(): return None
            noindex = "noindex" in response.headers.get(
                "X-Robots-Tag", ""
            ).lower()
            raw = self._read_limited(response, MAX_PAGE_BYTES)
            if len(raw) > MAX_PAGE_BYTES: return None
            return (
                normalize_url(final_url),
                raw.decode(
                    response.headers.get_content_charset() or "utf-8",
                    "replace",
                ),
                noindex,
            )

    def _run(self, initial: list[str]) -> None:
        queue, seen = list(initial), set()
        while (
            queue
            and self.indexed < self.max_pages
            and self.requests < self.max_requests
            and time.monotonic() < self._deadline
            and not self._stop.is_set()
        ):
            raw = queue.pop(0)
            try: url = normalize_url(raw)
            except ValueError: self.skipped += 1; continue
            if url in seen or not self._within_scope(url): self.skipped += 1; continue
            seen.add(url)
            allowed, delay = self._allowed(url)
            if not allowed: self.skipped += 1; continue
            try: fetched = self._fetch(url, delay)
            except Exception as exc: self.errors.append(f"{url}: {exc}"); continue
            if not fetched: self.skipped += 1; continue
            final_url, page, header_noindex = fetched; parser = _PageParser(); parser.feed(page)
            canonical = urljoin(final_url, parser.canonical) if parser.canonical else final_url
            if not self._within_scope(canonical):
                canonical = final_url
            if (
                not self._stop.is_set()
                and not header_noindex
                and not parser.noindex
                and parser.text
            ):
                try:
                    self.engine.upsert_document(canonical, parser.title.strip() or canonical,
                        " ".join(parser.text), description=parser.description, source="crawler")
                    self.indexed += 1
                except ValueError: self.skipped += 1
            for link in parser.links:
                candidate = urldefrag(urljoin(final_url, link))[0]
                if (
                    self._within_scope(candidate)
                    and candidate not in seen
                    and len(queue) < self.max_pages * 5
                ):
                    queue.append(candidate)


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Reject redirects before urllib opens an unsafe destination."""

    def __init__(self, validator) -> None:
        super().__init__()
        self.validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urljoin(req.full_url, newurl)
        if not self.validator(target):
            raise ValueError("redirect blocked by crawler network policy")
        return super().redirect_request(req, fp, code, msg, headers, target)