import unittest

from browser_utils import display_url, normalize_user_input


class AddressInputTests(unittest.TestCase):
    def test_domain_gets_https(self):
        self.assertEqual(
            normalize_user_input("example.com/docs"),
            "https://example.com/docs",
        )

    def test_existing_scheme_is_kept(self):
        self.assertEqual(
            normalize_user_input("http://localhost:8000"),
            "http://localhost:8000",
        )

    def test_words_become_search(self):
        self.assertEqual(
            normalize_user_input("bottom browser"),
            "bottom://search/?q=bottom+browser",
        )

    def test_script_scheme_becomes_search(self):
        self.assertTrue(
            normalize_user_input("javascript:alert(1)").startswith(
                "bottom://search/"
            )
        )

    def test_bottom_search_display_shows_query(self):
        self.assertEqual(
            display_url("bottom://search/?q=independent+search"),
            "independent search",
        )

    def test_display_hides_http_scheme(self):
        self.assertEqual(
            display_url("https://example.com/path?q=1"),
            "example.com/path?q=1",
        )


if __name__ == "__main__":
    unittest.main()