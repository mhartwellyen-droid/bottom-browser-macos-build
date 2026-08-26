"""Build a resumable, attributed English Wikipedia starter corpus.

The Wikimedia Action API is queried in deterministic ``allpages`` order.  Each
completed batch is immediately written to the output and continuation state,
so an interrupted run resumes without losing verified records.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


API_URL = "https://en.wikipedia.org/w/api.php"
LICENSE = "CC BY-SA 4.0"
USER_AGENT = "BottomSearchCorpusBuilder/1.0 (+local corpus generation)"
WS_RE = re.compile(r"\s+")


def _read_records(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON list")
    records: dict[str, dict[str, str]] = {}
    for item in data:
        if not isinstance(item, dict) or not all(item.get(k) for k in ("url", "title", "text")):
            continue
        records[str(item["url"])] = {
            "url": str(item["url"]), "title": str(item["title"]),
            "description": str(item.get("description", "")),
            "text": str(item["text"]), "source": str(item.get("source") or item["url"]),
            "license": str(item.get("license") or LICENSE),
        }
    return records


def _atomic_json(path: Path, data: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _fetch(params: dict[str, str], retries: int = 6) -> dict[str, Any]:
    url = API_URL + "?" + urlencode(params)
    for attempt in range(retries):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            if "error" in data:
                raise RuntimeError(data["error"].get("info", "Wikimedia API error"))
            return data
        except Exception as exc:
            if attempt + 1 == retries:
                raise
            # API overload responses usually include an explicit cooldown.
            # Honor it so a long resumable job does not amplify throttling.
            retry_after = exc.headers.get("Retry-After") if isinstance(exc, HTTPError) else None
            try:
                wait = float(retry_after) if retry_after else 2 ** attempt
            except ValueError:
                wait = 2 ** attempt
            time.sleep(min(max(wait, 1.0), 60.0))
    raise AssertionError("unreachable")


def _record(page: dict[str, Any], *, title_only: bool = False) -> dict[str, str] | None:
    title = WS_RE.sub(" ", str(page.get("title", "")).strip())
    text = WS_RE.sub(" ", str(page.get("extract", "")).strip())
    if title_only and title and not text:
        text = title
    if page.get("missing") or not title or not text:
        return None
    # Namespace 0 pages only are requested; this URL is the source and
    # attribution target for the CC BY-SA record.
    url = "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="()'-,.")
    description = WS_RE.sub(" ", str(page.get("description", "")).strip())
    return {"url": url, "title": title, "description": description, "text": text,
            "source": url, "license": LICENSE}


def build(
    output: Path,
    state_path: Path,
    target: int,
    batch_size: int,
    delay: float,
    *,
    title_only: bool = False,
    random_pages: bool = False,
) -> int:
    records = _read_records(output)
    state: dict[str, Any] = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    continuation = state.get("continue")
    batches = int(state.get("batches", 0))
    while len(records) < target:
        params = {"action": "query", "format": "json", "formatversion": "2"}
        if random_pages:
            params.update({"generator": "random", "grnnamespace": "0",
                           "grnlimit": str(batch_size)})
        else:
            params.update({"generator": "allpages", "gapnamespace": "0",
                           "gaplimit": str(batch_size)})
        if not title_only:
            if not random_pages:
                params["gapfilterredir"] = "nonredirects"
            params.update({"prop": "extracts|description", "exintro": "1",
                           "explaintext": "1", "exlimit": "max"})
        if continuation and not random_pages:
            params.update(continuation)
        payload = _fetch(params)
        pages = payload.get("query", {}).get("pages", [])
        added = 0
        for page in pages:
            item = _record(page, title_only=title_only)
            if item and item["url"] not in records:
                records[item["url"]] = item
                added += 1
                if len(records) >= target:
                    break
        batches += 1
        continuation = payload.get("continue")
        _atomic_json(output, [records[url] for url in sorted(records)])
        _atomic_json(state_path, {"continue": continuation, "batches": batches,
                                  "records": len(records), "license": LICENSE})
        print(f"batch {batches}: added {added}; total {len(records)}", file=sys.stderr)
        if (not random_pages and not continuation) or not pages:
            break
        if delay:
            time.sleep(delay)
    return len(records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("starter_corpus.json"))
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--target", type=int, default=25_000)
    parser.add_argument("--batch-size", type=int, default=500, choices=range(1, 501))
    parser.add_argument("--delay", type=float, default=0.25,
                        help="seconds between completed API batches (default: 0.25)")
    parser.add_argument(
        "--title-only",
        action="store_true",
        help="quickly add attributed title records without article extracts",
    )
    parser.add_argument(
        "--random",
        action="store_true",
        help="sample broadly across Wikipedia instead of alphabetical order",
    )
    args = parser.parse_args()
    if args.target < 1:
        parser.error("--target must be positive")
    state = args.state or args.output.with_suffix(args.output.suffix + ".state.json")
    try:
        count = build(
            args.output,
            state,
            args.target,
            args.batch_size,
            max(0.0, args.delay),
            title_only=args.title_only,
            random_pages=args.random,
        )
    except Exception as exc:
        print(f"Corpus generation stopped safely: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {count} attributed {LICENSE} records to {args.output}")
    return 0 if count >= args.target else 1


if __name__ == "__main__":
    raise SystemExit(main())