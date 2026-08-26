import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_client
from PyQt6.QtCore import QCoreApplication


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int) -> bytes:
        chunk = self.content[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


class LocalAIModelTests(unittest.TestCase):
    def test_verified_model_download_is_promoted_atomically(self) -> None:
        content = b"verified local model"
        expected = hashlib.sha256(content).hexdigest()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(ai_client, "model_directory", return_value=Path(directory)),
            patch.object(ai_client, "MODEL_BYTES", len(content)),
            patch.object(ai_client, "MODEL_SHA256", expected),
            patch.object(
                ai_client, "urlopen", return_value=_FakeResponse(content)
            ),
        ):
            worker = ai_client.AIRequestThread("hello")
            result = worker._ensure_model_file()
            self.assertEqual(content, result.read_bytes())
            self.assertFalse(result.with_suffix(".part").exists())

    def test_context_is_local_and_bounded(self) -> None:
        worker = ai_client.AIRequestThread(
            "summarize",
            [{
                "title": "Title",
                "url": "https://example.com",
                "snippet": "page text",
            }],
        )
        message = worker._user_message()
        self.assertIn("User request: summarize", message)
        self.assertIn("Text: page text", message)
        self.assertIn("Do not claim you visited", message)

    def test_generation_stops_cooperatively_when_interrupted(self) -> None:
        worker = ai_client.AIRequestThread("hello")
        answers: list[str] = []

        class FakeModel:
            def create_chat_completion(self, **_kwargs):
                yield {"choices": [{"delta": {"content": "partial"}}]}
                worker.requestInterruption()
                yield {"choices": [{"delta": {"content": "ignored"}}]}

        worker.completed.connect(answers.append)
        with (
            patch.object(worker, "_ensure_model_file", return_value=Path("model")),
            patch.object(worker, "_load_model", return_value=FakeModel()),
        ):
            app = QCoreApplication.instance() or QCoreApplication([])
            worker.start()
            self.assertTrue(worker.wait(2_000))
            app.processEvents()
        self.assertEqual([], answers)


if __name__ == "__main__":
    unittest.main()