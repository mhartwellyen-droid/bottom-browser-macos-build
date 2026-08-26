"""HTML views for Bottom Search's private, on-device results."""

from __future__ import annotations

from html import escape
from typing import Any, Iterable, Mapping


SHELL_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  color: #e8ecf7;
  background:
    radial-gradient(circle at 50% 8%, rgba(112,91,255,.11), transparent 28%),
    #0b0d13;
  font-family: Inter, "SF Pro Display", "Segoe UI", system-ui, sans-serif;
}
a { color: inherit; }
.mark {
  display: inline-grid;
  place-items: center;
  width: 46px;
  height: 46px;
  flex: 0 0 auto;
  border-radius: 15px;
  background: linear-gradient(145deg, #7357ff, #2ec6ff);
  box-shadow: 0 14px 36px rgba(91,75,255,.24);
}
.mark::after {
  content: "";
  width: 19px;
  height: 19px;
  border: 5px solid white;
  border-top-color: transparent;
  border-radius: 50%;
  transform: rotate(35deg);
}
.search {
  display: flex;
  align-items: center;
  width: min(680px, calc(100vw - 40px));
  height: 54px;
  padding: 0 7px 0 20px;
  background: #181c27;
  border: 1px solid #30364a;
  border-radius: 17px;
  box-shadow: 0 16px 45px rgba(0,0,0,.24);
}
.search:focus-within { border-color: #6d5dfc; }
.search input {
  min-width: 0;
  flex: 1;
  height: 100%;
  color: #f5f6fa;
  background: transparent;
  border: 0;
  outline: 0;
  font: 16px/1.2 inherit;
}
.search button {
  height: 40px;
  padding: 0 18px;
  color: white;
  background: linear-gradient(135deg, #745cff, #526fff);
  border: 0;
  border-radius: 12px;
  font: 650 13px/1 inherit;
  cursor: pointer;
}
.search button:hover { filter: brightness(1.1); }
.muted { color: #7f879b; }
"""


def _value(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _stats_value(stats: Any, name: str, default: int = 0) -> int:
    value = _value(stats, name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_snippet(value: str) -> str:
    safe = escape(value or "")
    return (
        safe.replace("&lt;mark&gt;", "<mark>")
        .replace("&lt;/mark&gt;", "</mark>")
        .replace("&lt;b&gt;", "<mark>")
        .replace("&lt;/b&gt;", "</mark>")
    )


def render_new_tab(stats: Any) -> str:
    document_count = _stats_value(
        stats, "document_count", _stats_value(stats, "documents")
    )
    index_note = (
        f"{document_count:,} pages ready in your private index"
        if document_count
        else "Preparing your private starter index"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>New Tab</title>
  <style>
    {SHELL_CSS}
    body {{ display: grid; place-items: center; overflow: hidden; }}
    main {{ width: min(760px, calc(100vw - 40px)); text-align: center; transform: translateY(-3vh); }}
    .hero-mark {{ width: 78px; height: 78px; margin-bottom: 24px; border-radius: 25px; }}
    .hero-mark::after {{ width: 32px; height: 32px; border-width: 7px; }}
    h1 {{ margin: 0 0 24px; font-size: clamp(34px, 5vw, 54px); letter-spacing: -.05em; }}
    .search {{ margin: 0 auto; }}
    p {{ margin: 17px 0 0; color: #7f879b; font-size: 13px; }}
    .private {{
      display: inline-flex; align-items: center; gap: 7px; margin-top: 28px;
      padding: 7px 11px; color: #929bb0; background: #141821;
      border: 1px solid #252b3a; border-radius: 999px; font-size: 12px;
    }}
    .dot {{ width: 7px; height: 7px; border-radius: 50%; background: #43d6a5; box-shadow: 0 0 12px #43d6a5; }}
  </style>
</head>
<body>
  <main>
    <div class="mark hero-mark" aria-hidden="true"></div>
    <h1>Search your way.</h1>
    <form class="search" action="bottom://search/" method="get">
      <input name="q" autofocus autocomplete="off" placeholder="Search Bottom or enter an address">
      <button type="submit">Search</button>
    </form>
    <p>{escape(index_note)}</p>
    <div class="private"><span class="dot"></span>Independent index · Searches stay on this Mac</div>
  </main>
</body>
</html>"""


def render_results(
    query: str,
    results: Iterable[Any],
    stats: Any,
    elapsed_ms: float = 0,
) -> str:
    result_list = list(results)
    document_count = _stats_value(
        stats, "document_count", _stats_value(stats, "documents")
    )
    cards: list[str] = []
    seen_domains: dict[str, int] = {}
    for result in result_list:
        url = str(_value(result, "url"))
        title = str(_value(result, "title") or url)
        domain = str(_value(result, "domain"))
        snippet = str(
            _value(result, "snippet")
            or _value(result, "description")
            or "Indexed by Bottom Search."
        )
        source = str(_value(result, "source", "Bottom index"))
        license_name = str(_value(result, "license"))
        attribution = (
            f"{license_name} · {source}" if license_name else source
        )
        seen_domains[domain] = seen_domains.get(domain, 0) + 1
        cards.append(
            f"""
            <article class="result">
              <a class="result-link" href="{escape(url, quote=True)}">
                <div class="source-row">
                  <span class="favicon">{escape((domain[:1] or "B").upper())}</span>
                  <span><strong>{escape(domain or "Local document")}</strong><small>{escape(url)}</small></span>
                </div>
                <h2>{escape(title)}</h2>
              </a>
              <p>{_safe_snippet(snippet)}</p>
              <span class="source-tag">{escape(attribution)}</span>
            </article>"""
        )

    if not cards:
        cards.append(
            f"""
            <section class="empty">
              <div class="empty-icon">⌕</div>
              <h2>No indexed pages match “{escape(query)}” yet</h2>
              <p>Bottom Search is independent, so its knowledge grows as its private crawler indexes more pages.</p>
              <p>Try broader words, check the spelling, or start an index refresh from the browser menu.</p>
            </section>"""
        )

    result_summary = (
        f"{len(result_list)} results from {document_count:,} indexed pages"
        + (f" · {elapsed_ms:.0f} ms" if elapsed_ms else "")
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(query)} — Bottom Search</title>
  <style>
    {SHELL_CSS}
    header {{
      position: sticky; top: 0; z-index: 2; display: flex; align-items: center; gap: 16px;
      padding: 20px max(28px, calc((100vw - 1080px)/2)); background: rgba(11,13,19,.9);
      border-bottom: 1px solid #202533; backdrop-filter: blur(18px);
    }}
    header .search {{ width: min(680px, calc(100vw - 130px)); }}
    main {{ width: min(760px, calc(100vw - 48px)); margin: 27px auto 80px; }}
    .summary {{ margin: 0 0 23px; color: #798197; font-size: 12px; }}
    .result {{ padding: 5px 0 27px; margin-bottom: 21px; border-bottom: 1px solid #1d2230; }}
    .result-link {{ text-decoration: none; }}
    .source-row {{ display: flex; align-items: center; gap: 10px; color: #aeb5c7; font-size: 12px; }}
    .source-row span:last-child {{ min-width: 0; }}
    .source-row strong {{ display: block; color: #bbc2d2; font-weight: 580; }}
    .source-row small {{
      display: block; max-width: 610px; margin-top: 2px; overflow: hidden;
      color: #6f788e; text-overflow: ellipsis; white-space: nowrap;
    }}
    .favicon {{
      display: grid; place-items: center; width: 34px; height: 34px; flex: 0 0 auto;
      color: #dcd8ff; background: #242039; border: 1px solid #393257; border-radius: 10px;
      font-weight: 750;
    }}
    h2 {{ margin: 12px 0 7px; color: #a99bff; font-size: 20px; line-height: 1.25; font-weight: 630; }}
    .result-link:hover h2 {{ color: #c1b7ff; text-decoration: underline; }}
    .result p {{ margin: 0; color: #a4acbd; font-size: 14px; line-height: 1.62; }}
    mark {{ padding: 0 2px; color: #edf0f7; background: rgba(115,87,255,.28); border-radius: 3px; }}
    .source-tag {{
      display: inline-block; margin-top: 10px; padding: 3px 7px; color: #737d92;
      background: #131720; border: 1px solid #222837; border-radius: 6px; font-size: 10px;
    }}
    .ai-answer {{
      margin: 0 0 27px; padding: 19px 21px; color: #c8cde0;
      background: linear-gradient(135deg, #241d44, #153342);
      border: 1px solid #4e4d83; border-radius: 16px; line-height: 1.6;
    }}
    .ai-answer strong {{ display:block; margin-bottom:7px; color:#83e0e7; }}
    .ai-answer[data-error="true"] {{ color:#e8a9bd; border-color:#6a3b54; }}
    .empty {{ padding: 72px 30px; text-align: center; background: #11151e; border: 1px solid #232938; border-radius: 20px; }}
    .empty-icon {{ color: #8272ff; font-size: 52px; }}
    .empty h2 {{ color: #e7eaf2; }}
    .empty p {{ max-width: 560px; margin: 10px auto; color: #858da3; line-height: 1.55; }}
  </style>
</head>
<body>
  <header>
    <a class="mark" href="bottom://newtab/" title="Bottom Search"></a>
    <form class="search" action="bottom://search/" method="get">
      <input name="q" value="{escape(query, quote=True)}" autocomplete="off">
      <button type="submit">Search</button>
    </form>
  </header>
  <main>
    <p class="summary">{escape(result_summary)}</p>
    <section class="ai-answer" id="bottom-ai-answer">
      <strong>Bottom AI</strong>
      <span id="bottom-ai-text">Reading the local results…</span>
    </section>
    {''.join(cards)}
  </main>
</body>
</html>"""


def render_error(message: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Bottom Search</title>
<style>{SHELL_CSS}body{{display:grid;place-items:center}}main{{max-width:620px;padding:40px;text-align:center}}</style>
</head><body><main><div class="mark"></div><h1>Bottom Search needs a moment</h1>
<p class="muted">{escape(message)}</p><p><a href="bottom://newtab/">Return to a new tab</a></p></main></body></html>"""