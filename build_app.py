"""Cross-platform PyInstaller build entry point for Bottom Browser."""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_NAME = "BottomBrowser"
BUNDLE_IDENTIFIER = "com.bottombrowser.app"


def project_version() -> str:
    """Read the release version without adding a packaging dependency."""
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        return tomllib.load(project_file)["project"]["version"]


def macos_architecture() -> str:
    """Return the stable architecture label used in macOS artifact names."""
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)


def add_optional_resources(command: list[str]) -> None:
    """Include resources which are optional in source checkouts."""
    corpus = ROOT / "starter_corpus.json"
    if corpus.is_file():
        command.extend(["--add-data", f"{corpus}{os.pathsep}."])

    if sys.platform == "darwin":
        icons = (ROOT / "assets" / "BottomBrowser.icns", ROOT / "BottomBrowser.icns")
        for icon in icons:
            if icon.is_file():
                command.extend(["--icon", str(icon)])
                break
        else:
            from make_macos_icon import create_icns

            icon = create_icns(ROOT / "build" / "BottomBrowser.icns")
            command.extend(["--icon", str(icon)])


def update_macos_bundle_metadata(app_bundle: Path, version: str) -> None:
    """Set the values Finder and macOS display for the generated app bundle."""
    info_path = app_bundle / "Contents" / "Info.plist"
    with info_path.open("rb") as info_file:
        info = plistlib.load(info_file)

    info.update(
        {
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": BUNDLE_IDENTIFIER,
            "CFBundleName": APP_NAME,
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSHighResolutionCapable": True,
        }
    )
    with info_path.open("wb") as info_file:
        plistlib.dump(info, info_file)


def create_macos_dmg(app_bundle: Path, version: str) -> Path:
    """Create a Finder-friendly DMG containing the app and Applications link."""
    architecture = macos_architecture()
    staging = ROOT / "build" / "dmg-root"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copytree(app_bundle, staging / app_bundle.name, symlinks=True)
    (staging / "Applications").symlink_to("/Applications")

    dmg = ROOT / "dist" / f"{APP_NAME}-{version}-macos-{architecture}.dmg"
    subprocess.run(
        [
            "hdiutil",
            "create",
            "-volname",
            APP_NAME,
            "-srcfolder",
            str(staging),
            "-ov",
            "-format",
            "UDZO",
            str(dmg),
        ],
        check=True,
    )
    return dmg


def main() -> int:
    version = project_version()
    for folder in ("build", "dist"):
        shutil.rmtree(ROOT / folder, ignore_errors=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        APP_NAME,
    ]
    if sys.platform == "darwin":
        command.extend(
            [
                "--osx-bundle-identifier",
                BUNDLE_IDENTIFIER,
                "--target-architecture",
                macos_architecture(),
            ]
        )
    add_optional_resources(command)
    command.append(str(ROOT / "main.py"))

    print(f"Building {APP_NAME} {version} for {sys.platform}…")
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode:
        return completed.returncode

    output = ROOT / "dist" / APP_NAME
    if sys.platform == "darwin":
        output = ROOT / "dist" / f"{APP_NAME}.app"
        update_macos_bundle_metadata(output, version)
        dmg = create_macos_dmg(output, version)
        if os.environ.get("BOTTOM_BROWSER_EMBED_DMG") == "1":
            # Some generic CI templates only upload the .app bundle. Keeping a
            # copy inside Resources lets that bundle carry the real DMG out of
            # the runner without changing the template-owned workflow.
            shutil.copy2(dmg, output / "Contents" / "Resources" / dmg.name)
        print(f"DMG complete: {dmg}")
    elif sys.platform == "win32":
        output = output / f"{APP_NAME}.exe"
    print(f"Build complete: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())