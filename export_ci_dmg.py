"""Export a CI-built DMG through a private Git branch in safe-size chunks."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CHUNK_SIZE = 45 * 1024 * 1024


def _run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def export() -> str:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("DMG branch export is only intended for GitHub Actions")

    architecture = {
        "amd64": "x86_64",
        "aarch64": "arm64",
    }.get(platform.machine().lower(), platform.machine().lower())
    matches = list((ROOT / "dist").glob(f"*-macos-{architecture}.dmg"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {architecture} DMG, found {matches}")
    dmg = matches[0]

    with tempfile.TemporaryDirectory(prefix="bottom-browser-dmg-") as temp:
        staging = Path(temp) / "dmg"
        staging.mkdir()
        digest = hashlib.sha256()
        chunks: list[dict[str, int | str]] = []
        with dmg.open("rb") as source:
            index = 1
            while data := source.read(CHUNK_SIZE):
                digest.update(data)
                name = f"{dmg.name}.part-{index:03d}"
                (staging / name).write_bytes(data)
                chunks.append({"name": name, "bytes": len(data)})
                index += 1

        manifest = {
            "filename": dmg.name,
            "architecture": architecture,
            "bytes": dmg.stat().st_size,
            "sha256": digest.hexdigest(),
            "chunks": chunks,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )

        branch = f"dmg-output-{architecture}"
        _run("git", "config", "user.name", "Bottom Browser Builder")
        _run("git", "config", "user.email", "actions@users.noreply.github.com")
        _run("git", "checkout", "--orphan", branch)
        _run("git", "rm", "-rf", ".")
        shutil.copytree(staging, ROOT / "dmg")
        _run("git", "add", "dmg")
        _run("git", "commit", "-m", f"Export {architecture} DMG")
        _run("git", "push", "origin", f"HEAD:refs/heads/{branch}", "--force")
        return branch


if __name__ == "__main__":
    print(export())