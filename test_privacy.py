"""Unit tests for local privacy decisions; no Qt event loop or network needed."""

import unittest

from privacy import PrivacyRequestInterceptor, youtube_dislike_injection_js


class FakeRequest:
    def __init__(self, url, first_party):
        self._url, self._first_party, self.blocked = url, first_party, False

    def requestUrl(self):
        return self._url

    def firstPartyUrl(self):
        return self._first_party

    def block(self, value):
        self.blocked = value


class PrivacyInterceptorTests(unittest.TestCase):
    def setUp(self):
        self.blocker = PrivacyRequestInterceptor()

    def test_domain_rules_and_independent_toggles(self):
        self.assertEqual(self.blocker.should_block("https://cdn.doubleclick.net/x"), "ad")
        self.assertEqual(self.blocker.should_block("https://www.google-analytics.com/g/collect"), "tracker")
        self.blocker.set_ad_blocking(False)
        self.assertIsNone(self.blocker.should_block("https://doubleclick.net/ad"))
        self.assertEqual(self.blocker.should_block("https://segment.io/v1/track"), "tracker")
        self.blocker.set_tracker_blocking(False)
        self.assertIsNone(self.blocker.should_block("https://segment.io/v1/track"))

    def test_path_rules_and_lookalikes(self):
        self.assertEqual(self.blocker.classify("https://site.test/analytics/collect"), "tracker")
        self.assertEqual(self.blocker.classify("https://site.test/adserver/request"), "ad")
        self.assertIsNone(self.blocker.classify("https://notdoubleclick.net/content"))
        self.assertIsNone(self.blocker.classify("file:///tmp/analytics/collect"))

    def test_counts_are_per_page_and_total(self):
        first = "https://example.test/article#section"
        ad = FakeRequest("https://doubleclick.net/slot", first)
        tracker = FakeRequest("https://segment.io/v1/track", first)
        self.blocker.interceptRequest(ad)
        self.blocker.interceptRequest(tracker)
        self.assertTrue(ad.blocked)
        self.assertEqual(self.blocker.page_counts("https://example.test/article").total, 2)
        self.assertEqual(self.blocker.total_counts().ads, 1)
        self.assertEqual(self.blocker.total_counts().trackers, 1)
        self.blocker.reset_counts("https://example.test/article")
        self.assertEqual(self.blocker.page_counts(first).total, 0)
        self.assertEqual(self.blocker.total_counts().total, 2)


class YoutubeInjectionTests(unittest.TestCase):
    def test_script_is_guarded_and_attributed(self):
        script = youtube_dislike_injection_js()
        self.assertIn('url.pathname !== "/watch"', script)
        self.assertIn("returnyoutubedislikeapi.com/votes", script)
        self.assertIn("Return YouTube Dislike", script)
        self.assertIn("textContent", script)
        self.assertIn(".catch(() => {})", script)


if __name__ == "__main__":
    unittest.main()