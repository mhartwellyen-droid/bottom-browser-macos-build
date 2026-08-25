"""Local, private full-text search for Bottom Browser.

Public API: :class:`LocalSearchEngine` stores documents in a SQLite database.
Use ``upsert_document(url, title, text, ...)``, ``search(query)``,
``seed_starter_corpus()``, ``history()``, ``clear_history()``, ``clear_index()``,
and ``stats()``.  ``crawl(urls, ...)`` starts a daemon background crawl and
returns a :class:`Crawler`; it deliberately never performs network work on the
caller's/UI thread.  All functionality uses the Python standard library.
"""
from __future__ import annotations

import html
import json
import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urldefrag, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

USER_AGENT = "BottomSearchBot/1.0 (+local Bottom Browser search)"
MAX_PAGE_BYTES = 1_500_000
MAX_TEXT_CHARS = 250_000
BAD_EXTENSIONS = frozenset((".pdf", ".zip", ".gz", ".tar", ".jpg", ".jpeg", ".png",
    ".gif", ".webp", ".svg", ".mp3", ".mp4", ".avi", ".mov", ".exe", ".dmg",
    ".iso", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".woff", ".ttf"))
TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)

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
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list): raise ValueError("starter corpus must be a JSON list")
        for item in entries:
            self.upsert_document(item["url"], item["title"], item["text"],
                description=item.get("description", ""), source=item.get("source", ""),
                license=item.get("license", ""))
        return len(entries)

    def search(self, query: str, limit: int = 10, *, record_history: bool = True) -> list[SearchResult]:
        """Search with BM25, freshness and diversity.

        All terms are required first.  If that produces no documents, a safe
        OR/prefix query is used so partially typed and broad multi-word
        searches remain useful.  Only tokens extracted by :data:`TOKEN_RE`
        enter FTS syntax, so user input cannot alter the query grammar.
        """
        terms = TOKEN_RE.findall(query.lower())
        if not terms: return []
        limit = max(1, min(int(limit), 100))
        with self._lock:
            if record_history:
                with self._db: self._db.execute("INSERT INTO query_history(query,searched_at) VALUES (?,?)", (query.strip()[:500], _utcnow()))
            rows = self._fts_rows(" ".join(f'"{term}"' for term in terms), limit)
            if not rows:
                # Prefix terms intentionally improve first-run discovery (for
                # example, "astron phy" can find astronomy and physics).
                fallback = " OR ".join(f'"{term}"*' for term in terms)
                rows = self._fts_rows(fallback, limit)
        seen: dict[str, int] = {}
        results = []
        now = datetime.now(timezone.utc)
        for row in rows:
            age = 3650.0
            try: age = max(0, (now - datetime.fromisoformat(row["updated_at"])).days)
            except ValueError: pass
            count = seen.get(row["domain"], 0); seen[row["domain"]] = count + 1
            # FTS bm25 is negative (more negative is better).
            score = -float(row["rank"]) + .25 / (1 + age / 30) - .18 * count
            results.append(SearchResult(row["url"], row["title"], self._snippet(row["text"], terms),
                                        score, row["domain"], row["description"], row["source"],
                                        row["license"], row["updated_at"]))
        return sorted(results, key=lambda r: r.score, reverse=True)[:limit]

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
        with self._lock, self._db: self._db.execute("DELETE FROM documents")
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
                 timeout: float = 10.0) -> None:
        self.engine, self.max_pages, self.same_host, self.timeout = engine, max(1, max_pages), same_host, timeout
        self.indexed = self.skipped = 0
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request: dict[str, float] = {}
    def start(self, urls: Iterable[str]) -> "Crawler":
        initial = list(urls)
        self._thread = threading.Thread(target=self._run, args=(initial,), daemon=True, name="BottomSearchCrawler")
        self._thread.start(); return self
    def join(self, timeout: float | None = None) -> bool:
        if self._thread: self._thread.join(timeout)
        return not self.running
    @property
    def running(self) -> bool: return bool(self._thread and self._thread.is_alive())
    def stop(self) -> None: self._stop.set()
    def _allowed(self, url: str) -> tuple[bool, float]:
        p = urlsplit(url); host = p.netloc
        if p.scheme not in ("http", "https") or p.path.lower().endswith(tuple(BAD_EXTENSIONS)): return False, 0
        rp = self._robots.get(host)
        if rp is None:
            rp = RobotFileParser(); rp.set_url(f"{p.scheme}://{host}/robots.txt")
            try:
                # RobotFileParser.read cannot set a user agent. Fetch explicitly
                # so every HTTP request identifies this crawler consistently.
                request = Request(rp.url, headers={"User-Agent": USER_AGENT})
                with urlopen(request, timeout=self.timeout) as response:
                    if response.status >= 400:
                        rp.parse(["User-agent: *", "Allow: /"])
                    else:
                        rp.parse(response.read(256_000).decode(
                            response.headers.get_content_charset() or "utf-8", "replace"
                        ).splitlines())
            except Exception:
                # An unavailable robots file is treated as no published policy;
                # a response that explicitly disallows this bot is never ignored.
                rp.parse(["User-agent: *", "Allow: /"])
            self._robots[host] = rp
            self._last_request[host] = time.monotonic()
        return rp.can_fetch(USER_AGENT, url), (rp.crawl_delay(USER_AGENT) or rp.crawl_delay("*") or 1.0)
    def _fetch(self, url: str, delay: float) -> tuple[str, str] | None:
        host = urlsplit(url).netloc; wait = delay - (time.monotonic() - self._last_request.get(host, 0))
        if wait > 0: time.sleep(wait)
        self._last_request[host] = time.monotonic()
        req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
        with urlopen(req, timeout=self.timeout) as response:
            if "html" not in response.headers.get_content_type(): return None
            raw = response.read(MAX_PAGE_BYTES + 1)
            if len(raw) > MAX_PAGE_BYTES: return None
            return response.geturl(), raw.decode(response.headers.get_content_charset() or "utf-8", "replace")
    def _run(self, initial: list[str]) -> None:
        queue, seen = list(initial), set()
        seed_hosts = {_domain(u) for u in initial if urlsplit(u).scheme in ("http", "https")}
        while queue and self.indexed < self.max_pages and not self._stop.is_set():
            raw = queue.pop(0)
            try: url = normalize_url(raw)
            except ValueError: self.skipped += 1; continue
            if url in seen or (self.same_host and _domain(url) not in seed_hosts): self.skipped += 1; continue
            seen.add(url)
            allowed, delay = self._allowed(url)
            if not allowed: self.skipped += 1; continue
            try: fetched = self._fetch(url, delay)
            except Exception as exc: self.errors.append(f"{url}: {exc}"); continue
            if not fetched: self.skipped += 1; continue
            final_url, page = fetched; parser = _PageParser(); parser.feed(page)
            canonical = urljoin(final_url, parser.canonical) if parser.canonical else final_url
            if not parser.noindex and parser.text:
                try:
                    self.engine.upsert_document(canonical, parser.title.strip() or canonical,
                        " ".join(parser.text), description=parser.description, source="crawler")
                    self.indexed += 1
                except ValueError: self.skipped += 1
            for link in parser.links:
                candidate = urldefrag(urljoin(final_url, link))[0]
                if candidate not in seen and len(queue) < self.max_pages * 20: queue.append(candidate)