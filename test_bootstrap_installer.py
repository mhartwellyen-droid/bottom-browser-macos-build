import tempfile
import unittest
from pathlib import Path

from build_bootstrap_installer import read_checksum


class BootstrapChecksumTests(unittest.TestCase):
    def test_reads_pinned_architecture_checksum(self) -> None:
        digest = "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "BottomBrowser-3.0.0-macos-arm64.dmg.sha256"
            )
            path.write_text(f"{digest}  package.dmg\n", encoding="utf-8")
            self.assertEqual(
                digest, read_checksum(Path(directory), "3.0.0", "arm64")
            )

    def test_rejects_invalid_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "BottomBrowser-3.0.0-macos-x86_64.dmg.sha256"
            )
            path.write_text("not-a-digest\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_checksum(Path(directory), "3.0.0", "x86_64")


if __name__ == "__main__":
    unittest.main()