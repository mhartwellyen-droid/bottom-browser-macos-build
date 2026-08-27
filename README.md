# Bottom Browser

A private desktop web browser built with Python, PyQt6, Chromium WebEngine, and
its own SQLite full-text search index. Tabs and navigation controls live at the
**bottom** of the window, leaving the top edge completely clear for page content.

## Features

- Bottom-mounted tab strip and navigation bar
- Bottom Search: an independent, on-device search engine with no forwarded
  queries, provider redirects, advertising, or tracking
- A broad starter knowledge index that works immediately after installation
- Polite background crawling that respects robots.txt, crawl delays, no-index
  directives, page-size limits, and canonical URLs
- BM25 relevance ranking with freshness and domain-diversity adjustments
- Back, forward, refresh, and stop controls
- Movable tabs, close buttons, middle-click close, and recently closed tabs
- Popups and `target="_blank"` links open in a new tab
- Persistent cookies and browser profile
- File downloads with a native save dialog
- Zoom controls and full-screen mode
- Modern dark interface and a custom new-tab screen
- Private search history and controls to refresh, clear, or reset the index
- Private on-device Bottom AI with no account or API key
- Independent ad and tracker blocking, optional YouTube dislike counts, and
  battery-saving tab freezing

## Private Bottom AI

Bottom AI runs on the downloader's Mac through `llama.cpp`; prompts and local
search context are not sent to an AI provider. On first use, Bottom Browser
automatically downloads the Apache-2.0-licensed SmolLM2 360M Instruct model
(about 258 MiB), verifies its pinned SHA-256 digest, and stores it in the app
data directory. Later requests work offline. Page text is given to the model
only through the explicit **Share page text & summarize** action.

Model and inference-engine attribution is in `THIRD_PARTY_NOTICES.md`.

## How Bottom Search works

Words typed into the bottom address bar open a branded `bottom://search` page.
The query is answered from a local SQLite FTS5 database stored on the user's
computer. It is never sent to DuckDuckGo, Google, Bing, Brave, or another search
provider.

The bundled starter corpus makes first-run searches useful. Choose **Refresh
private search index** from the browser menu to let the background crawler add
pages from the configured public seed sites. The crawler identifies itself as
`BottomSearchBot`, follows published crawl policies, and keeps the index local.

Starter reference summaries are attributed in `starter_corpus.json` and are
provided under their listed licenses, including CC BY-SA material attributed to
Wikipedia contributors.

## Run from source

Python 3.12 is recommended.

```bash
python -m pip install -r requirements.txt
python main.py
```

## Build a downloadable desktop app

Build on the operating system you want to distribute for:

```bash
python build_app.py
```

The packaged app is written to `dist/BottomBrowser`. On Windows it contains
`BottomBrowser.exe`. On macOS the script creates `BottomBrowser.app` and an
architecture-specific DMG:

- `BottomBrowser-3.0.2-macos-arm64.dmg` for Apple Silicon
- `BottomBrowser-3.0.2-macos-x86_64.dmg` for Intel Macs

The DMG contains the app and an Applications shortcut. Drag Bottom Browser into
Applications. Unsigned local builds may require right-clicking the app and
choosing **Open** the first time.

Qt WebEngine includes Chromium, so packaged builds are larger than a typical
Python utility.

## Automated macOS downloads

`.github/workflows/build-macos.yml` builds and smoke-tests both macOS
architectures on real GitHub-hosted Mac runners. Run **Build macOS packages**
from the repository's Actions tab, then download the two DMG artifacts and their
SHA-256 checksums.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+L` | Focus address bar |
| `Ctrl+T` | New tab |
| `Ctrl+W` | Close current tab |
| `Ctrl+Shift+T` | Reopen closed tab |
| `Ctrl+R` / `F5` | Refresh |
| `Alt+Left` / `Alt+Right` | Back / forward |
| `Ctrl++` / `Ctrl+-` / `Ctrl+0` | Zoom in / out / reset |
| `F11` | Full screen |

## Platform notes

- Build packages are platform-specific. Build on Windows for a Windows download,
  macOS for a macOS download, and Linux for a Linux download.
- On Linux, the packaged app requires a graphical desktop session.
- Browser profile data, Bottom Search history, and the local index are stored in
  `~/.bottom-browser`.
- The private AI model is stored in Bottom Browser's standard macOS application
  data directory and can be removed to reclaim about 258 MiB.
- Apple Developer ID signing and notarization are intentionally not included.