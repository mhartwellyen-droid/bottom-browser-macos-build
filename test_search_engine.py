"""Tests use only temporary SQLite files and no network."""
import json
import tempfile
import unittest
from pathlib import Path

from search_engine import LocalSearchEngine, normalize_url


class SearchEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = LocalSearchEngine(Path(self.tmp.name) / "search.sqlite")
    def tearDown(self):
        self.engine.close(); self.tmp.cleanup()
    def test_upsert_search_history_and_clear(self):
        self.engine.upsert_document("https://example.test/a#part", "Safe <title>",
                                    "A <script>bad</script> solar energy guide",
                                    source="https://example.test/source", license="CC0-1.0")
        self.engine.upsert_document("https://example.test/a", "Updated solar guide", "solar panels generate electricity")
        found = self.engine.search("solar")
        self.assertEqual(len(found), 1)
        self.assertIn("<mark>solar</mark>", found[0].snippet.lower())
        self.assertNotIn("<script>", found[0].snippet)
        self.assertEqual(found[0].source, "https://example.test/source")
        self.assertEqual(found[0].license, "CC0-1.0")
        self.assertTrue(found[0].updated_at)
        self.assertEqual(self.engine.stats()["documents"], 1)
        self.assertEqual(len(self.engine.history()), 1)
        self.engine.clear_history(); self.engine.clear_index()
        self.assertEqual(self.engine.stats(), {"documents": 0, "history": 0, "domains": 0})
    def test_seed_is_idempotent_and_url_validation(self):
        fixture = Path(self.tmp.name) / "corpus.json"
        fixture.write_text(json.dumps([{"url":"https://facts.test/one","title":"Fact","text":"ocean water science"}]))
        self.assertEqual(self.engine.seed_starter_corpus(fixture), 1)
        self.engine.seed_starter_corpus(fixture)
        self.assertEqual(self.engine.stats()["documents"], 1)
        with self.assertRaises(ValueError): self.engine.upsert_document("file:///tmp/x", "x", "x")
    def test_normalize_url(self):
        self.assertEqual(normalize_url("HTTPS://Example.TEST/a#x"), "https://example.test/a")

    def test_multi_word_falls_back_to_safe_or_prefix_matching(self):
        self.engine.upsert_document("https://example.test/space", "Astronomy", "astronomy studies stars")
        fallback = self.engine.search("astron oceans", record_history=False)
        self.assertEqual(fallback[0].title, "Astronomy")
        # Operators and punctuation are tokenized rather than executed as FTS syntax.
        self.assertEqual(self.engine.search('" OR *', record_history=False), [])


if __name__ == "__main__":
    unittest.main()