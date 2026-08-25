"""Generate Bottom Browser's macOS ICNS icon from its SVG source."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import QByteArray, QRectF
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parent
SVG = ROOT / "assets" / "BottomBrowser.svg"


def _render_png(renderer: QSvgRenderer, target: Path, pixels: int) -> None:
    image = QImage(
        pixels,
        pixels,
        QImage.Format.Format_RGBA8888,
    )
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, pixels, pixels))
    painter.end()
    if not image.save(str(target), "PNG"):
        raise RuntimeError(f"Could not write icon image: {target}")


def create_icns(output: Path) -> Path:
    """Create an iconset and compile it with Apple's iconutil."""
    if shutil.which("iconutil") is None:
        raise RuntimeError("iconutil is required and is only available on macOS")

    iconset = output.parent / "BottomBrowser.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    renderer = QSvgRenderer(QByteArray(SVG.read_bytes()))
    if not renderer.isValid():
        raise RuntimeError(f"Invalid SVG icon source: {SVG}")

    for points in (16, 32, 128, 256, 512):
        _render_png(renderer, iconset / f"icon_{points}x{points}.png", points)
        _render_png(
            renderer,
            iconset / f"icon_{points}x{points}@2x.png",
            points * 2,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(output)],
        check=True,
    )
    return output


if __name__ == "__main__":
    print(create_icns(ROOT / "build" / "BottomBrowser.icns"))