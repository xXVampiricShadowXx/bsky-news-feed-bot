import unittest

import feedparser

import feeds
import publisher


class SportFilterTests(unittest.TestCase):
    def test_skips_sport_by_tag_or_path(self):
        self.assertTrue(feeds.is_sport({"tags": [{"term": "Sport"}]}, "https://abc.net.au/news/1/x/2"))
        self.assertTrue(feeds.is_sport({}, "https://www.bbc.com/sport/football/123"))
        self.assertTrue(feeds.is_sport({}, "https://www.rnz.co.nz/news/sport/1"))
        self.assertFalse(feeds.is_sport({"tags": [{"term": "World Politics"}]}, "https://abc.net.au/news/2026/sportswashing-row/9"))


class WireCreditTests(unittest.TestCase):
    def test_detects_wire_agencies_in_bylines(self):
        cases = {
            "Michael Casey, Associated Press": "Associated Press",
            "The Associated Press": "Associated Press",
            "AP": "Associated Press",
            "Jane Doe, AP": "Associated Press",
            "Reuters": "Reuters",
            "FRANCE 24 with AFP": "AFP",
            "Australian Associated Press": "AAP",
            "Staff, The Canadian Press": "The Canadian Press",
            "Ken Sweet, Associated Press ; Reuters": "Associated Press and Reuters",
        }
        for byline, expected in cases.items():
            with self.subTest(byline=byline):
                self.assertEqual(feeds.wire_credit(byline), expected)

    def test_ignores_staff_bylines(self):
        for byline in ["", "Veronica Vela", "FRANCE24", "RFI", "Melissa Chemam with RFI", "Apple Smith"]:
            with self.subTest(byline=byline):
                self.assertIsNone(feeds.wire_credit(byline))

    def test_feed_entry_byline_is_extracted(self):
        parsed = feedparser.parse(
            """<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
            <item><title>Quake</title><link>https://example.org/a</link>
            <dc:creator>John Leicester, Associated Press</dc:creator></item>
            </channel></rss>"""
        )
        self.assertEqual(feeds.wire_credit(feeds._byline(parsed.entries[0])), "Associated Press")

    def test_feed_entry_uses_all_authors(self):
        entry = {
            "author": "Jane Doe",
            "authors": [{"name": "Jane Doe"}, {"name": "Reuters"}],
        }
        self.assertEqual(feeds._byline(entry), "Jane Doe ; Reuters")

    def test_feed_entry_falls_back_to_singular_author_without_author_names(self):
        entry = {"author": "Reuters", "authors": [{"name": ""}]}
        self.assertEqual(feeds._byline(entry), "Reuters")

    def test_multiple_authors_and_agencies_are_not_duplicated(self):
        byline = feeds._byline({"authors": [{"name": "Reuters"}, {"name": "Reuters and AFP"}]})
        self.assertEqual("Reuters and AFP", feeds.wire_credit(byline))


class AttributionLineTests(unittest.TestCase):
    def test_publisher_only(self):
        self.assertEqual(publisher.attribution_line("BBC News"), "Source: BBC News")

    def test_publisher_with_wire(self):
        self.assertEqual(
            publisher.attribution_line("PBS News", "Associated Press"),
            "Source: PBS News, with Associated Press",
        )

    def test_wire_not_repeated_when_it_is_the_publisher(self):
        self.assertEqual(publisher.attribution_line("Reuters", "Reuters"), "Source: Reuters")


class StableStoryKeyTests(unittest.TestCase):
    def test_dw_retitled_story_keeps_its_key(self):
        a = "https://www.dw.com/en/iraq-us-troops-withdraw-after-12-years/a-79462926?maca=en-rss-en-world-4025-rdf"
        b = "https://www.dw.com/en/iraq-us-troops-withdraw-as-baghdad-delays/a-79462926"
        self.assertEqual(feeds.canonical_story_key(a), feeds.canonical_story_key(b))
        self.assertEqual(feeds.canonical_story_key(a), "https://dw.com/a-79462926")

    def test_abc_retitled_story_keeps_its_key(self):
        a = "https://www.abc.net.au/news/2026-10-02/old-title/107221348"
        b = "https://www.abc.net.au/news/2026-10-02/new-title/107221348"
        self.assertEqual(feeds.canonical_story_key(a), feeds.canonical_story_key(b))

    def test_other_sites_unchanged(self):
        self.assertEqual(
            feeds.canonical_story_key("https://www.bbc.co.uk/news/articles/c123?at_medium=RSS"),
            feeds.canonical_story_key("https://bbc.co.uk/news/articles/c123?at_medium=RSS"),
        )
        self.assertNotEqual(
            feeds.canonical_story_key("https://www.bbc.co.uk/news/a/1"),
            feeds.canonical_story_key("https://www.bbc.co.uk/news/b/1"),
        )

    def test_distinct_ids_and_unrelated_hosts_remain_distinct(self):
        for a, b in [
            ("https://dw.com/en/title/a-123", "https://dw.com/en/title/a-124"),
            ("https://abc.net.au/news/title/123", "https://abc.net.au/news/title/124"),
            ("https://rte.ie/news/123-title", "https://rte.ie/news/124-title"),
            ("https://example.org/en/old/a-123", "https://example.org/en/new/a-123"),
        ]:
            with self.subTest(a=a, b=b):
                self.assertNotEqual(feeds.canonical_story_key(a), feeds.canonical_story_key(b))


if __name__ == "__main__":
    unittest.main()
