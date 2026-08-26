"""Private on-device language model for Bottom Browser."""

from __future__ import annotations

import hashlib
import os
import platform
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QStandardPaths, QThread, pyqtSignal


MODEL_NAME = "SmolLM2-360M-Instruct.Q4_K_M.gguf"
MODEL_URL = (
    "https://huggingface.co/QuantFactory/SmolLM2-360M-Instruct-GGUF/"
    f"resolve/main/{MODEL_NAME}"
)
MODEL_SHA256 = "75c4346ef9e855ed630f80078a2430cf63aaca599e340360998a313070fcdc47"
MODEL_BYTES = 270_590_592
_MODEL = None
_MODEL_PATH: Path | None = None
_MODEL_LOCK = threading.Lock()


def model_directory() -> Path:
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    path = Path(base or (Path.home() / ".bottom-browser")) / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AIRequestThread(QThread):
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(
        self,
        prompt: str,
        context: list[dict[str, str]] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.prompt = prompt[:4000]
        self.context = (context or [])[:8]

    def _ensure_model_file(self) -> Path:
        destination = model_directory() / MODEL_NAME
        if destination.is_file():
            if destination.stat().st_size == MODEL_BYTES and (
                sha256_file(destination) == MODEL_SHA256
            ):
                return destination
            destination.unlink()

        temporary = destination.with_suffix(".part")
        temporary.unlink(missing_ok=True)
        self.progress.emit("Downloading private AI model · 258 MB · one time")
        request = Request(
            MODEL_URL,
            headers={"User-Agent": "BottomBrowser/3.0"},
        )
        try:
            with urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                downloaded = 0
                last_milestone = -1
                while chunk := response.read(1024 * 1024):
                    if self.isInterruptionRequested():
                        raise InterruptedError("AI model download cancelled.")
                    output.write(chunk)
                    downloaded += len(chunk)
                    percent = min(100, int(downloaded * 100 / MODEL_BYTES))
                    milestone = percent // 5 * 5
                    if milestone != last_milestone:
                        last_milestone = milestone
                        self.progress.emit(
                            f"Downloading private AI model · {milestone}%"
                        )
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        self.progress.emit("Verifying private AI model…")
        if temporary.stat().st_size != MODEL_BYTES:
            temporary.unlink(missing_ok=True)
            raise ValueError("Downloaded AI model has the wrong size.")
        if sha256_file(temporary) != MODEL_SHA256:
            temporary.unlink(missing_ok=True)
            raise ValueError("Downloaded AI model failed its security check.")
        temporary.replace(destination)
        return destination

    def _load_model(self, path: Path):
        global _MODEL, _MODEL_PATH
        if _MODEL is not None and _MODEL_PATH == path:
            return _MODEL
        self.progress.emit("Loading private AI model…")
        from llama_cpp import Llama

        options = {
            "model_path": str(path),
            "n_ctx": 4096,
            "n_threads": max(2, (os.cpu_count() or 4) // 2),
            "verbose": False,
        }
        try:
            _MODEL = Llama(
                **options,
                n_gpu_layers=-1 if platform.system() == "Darwin" else 0,
            )
        except Exception:
            self.progress.emit("GPU unavailable · loading AI on CPU…")
            _MODEL = Llama(**options, n_gpu_layers=0)
        _MODEL_PATH = path
        return _MODEL

    def _user_message(self) -> str:
        if not self.context:
            return self.prompt
        sources = []
        for item in self.context:
            sources.append(
                f"Title: {item.get('title', '')[:300]}\n"
                f"URL: {item.get('url', '')[:1000]}\n"
                f"Text: {item.get('snippet', '')[:6000]}"
            )
        return (
            "Use the following local context when it is relevant. "
            "Do not claim you visited these URLs.\n\n"
            + "\n\n---\n\n".join(sources)
            + f"\n\nUser request: {self.prompt}"
        )

    def run(self) -> None:
        try:
            with _MODEL_LOCK:
                model = self._load_model(self._ensure_model_file())
                if self.isInterruptionRequested():
                    return
                self.progress.emit("Thinking privately on this Mac…")
                chunks = model.create_chat_completion(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are Bottom AI, a concise private assistant "
                                "running locally inside Bottom Browser. Be honest "
                                "about uncertainty and never invent sources."
                            ),
                        },
                        {"role": "user", "content": self._user_message()},
                    ],
                    max_tokens=384,
                    temperature=0.35,
                    top_p=0.9,
                    stream=True,
                )
                parts: list[str] = []
                for chunk in chunks:
                    if self.isInterruptionRequested():
                        return
                    text = chunk["choices"][0].get("delta", {}).get("content", "")
                    if text:
                        parts.append(str(text))
            answer = "".join(parts).strip()
            if not answer:
                raise ValueError("The local model returned an empty answer.")
            self.completed.emit(answer)
        except InterruptedError:
            return
        except (HTTPError, URLError, TimeoutError):
            self.failed.emit(
                "The private AI model could not be downloaded. Check your connection."
            )
        except ModuleNotFoundError:
            self.failed.emit("The local AI engine is missing from this build.")
        except Exception as exc:
            self.failed.emit(f"Bottom AI error: {exc}")