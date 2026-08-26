"""Create a tiny macOS installer DMG that fetches and verifies the full app."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def read_checksum(
    packages_dir: Path, version: str, architecture: str
) -> str:
    path = (
        packages_dir
        / f"BottomBrowser-{version}-macos-{architecture}.dmg.sha256"
    )
    digest = path.read_text(encoding="utf-8").split()[0].lower()
    if not SHA256_RE.fullmatch(digest):
        raise ValueError(f"Invalid SHA-256 in {path}")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--repository", default=os.environ.get("GITHUB_REPOSITORY", "")
    )
    parser.add_argument(
        "--packages-dir", type=Path, default=ROOT / "dist-release"
    )
    args = parser.parse_args()
    if not args.repository or "/" not in args.repository:
        parser.error("--repository must be an owner/repository pair")
    arm64_digest = read_checksum(args.packages_dir, args.version, "arm64")
    intel_digest = read_checksum(args.packages_dir, args.version, "x86_64")
    staging = ROOT / "build" / "installer-dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    script = staging / "Install Bottom Browser.command"
    script.write_text(
        f"""#!/bin/zsh
set -euo pipefail
VERSION={args.version!r}
BASE="https://github.com/{args.repository}/releases/download/v$VERSION"
case "$(uname -m)" in
  arm64) ARCH=arm64; EXPECTED={arm64_digest!r} ;;
  x86_64) ARCH=x86_64; EXPECTED={intel_digest!r} ;;
  *) echo "Unsupported Mac architecture: $(uname -m)"; read -k 1; exit 1 ;;
esac
DMG="BottomBrowser-$VERSION-macos-$ARCH.dmg"
WORK="$(mktemp -d)"
cleanup() {{ rm -rf "$WORK"; }}
trap cleanup EXIT
echo "Downloading the full Bottom Browser package for $ARCH…"
curl --fail --location --progress-bar "$BASE/$DMG" -o "$WORK/$DMG"
ACTUAL="$(shasum -a 256 "$WORK/$DMG" | awk '{{print $1}}')"
if [[ "$EXPECTED" != "$ACTUAL" ]]; then
  echo "Security check failed: the download checksum did not match."
  read -k 1
  exit 1
fi
echo "Verified. Opening the full installer…"
open "$WORK/$DMG"
echo
echo "Drag BottomBrowser to Applications. Because this release is unsigned,"
echo "right-click BottomBrowser and choose Open the first time."
echo "Press any key after you finish installing."
read -k 1
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    (staging / "README.txt").write_text(
        "Double-click “Install Bottom Browser.command”. It detects your Mac, "
        "downloads the matching full DMG from the official GitHub release, "
        "and verifies it against the SHA-256 checksum embedded when this compact "
        "installer was built. Get this compact installer from Replit Library.\n",
        encoding="utf-8",
    )
    output = ROOT / "dist" / f"BottomBrowser-Installer-{args.version}.dmg"
    output.parent.mkdir(exist_ok=True)
    subprocess.run(
        [
            "hdiutil", "create", "-volname", "Bottom Browser Installer",
            "-srcfolder", str(staging), "-ov", "-format", "UDZO", str(output),
        ],
        check=True,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())