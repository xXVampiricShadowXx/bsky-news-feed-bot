import gzip
import io
import unittest
from unittest.mock import MagicMock, patch

import feeds
import netsafe

RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>UN News</title><link>https://news.un.org/en/</link>
<item><title>Security Council meets on Sudan ceasefire</title>
<link>https://news.un.org/en/story/2026/10/1</link>
<dc:creator>Jane Doe, Associated Press</dc:creator></item>
</channel></rss>"""


class FakeResponse(io.BytesIO):
    headers: dict = {}

    def geturl(self):
        return "https://news.un.org/feed/subscribe/en/news/all/rss.xml"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FetchTests(unittest.TestCase):
    def test_plain_feed_extracts_credit_and_classifies_sport(self):
        rss = b"""<rss version="2.0"><channel><title>News</title>
        <item><title>World news</title><link>https://example.com/world</link>
        <author>Jane Doe, Reuters</author></item>
        <item><title>Sport news</title><link>https://example.com/sport/1</link></item>
        <item><title>More sport</title><link>https://example.com/other</link>
        <category>Sports</category></item></channel></rss>"""
        opener = MagicMock()
        opener.open.return_value = FakeResponse(rss)
        with patch.object(feeds, "_public_url_opener", return_value=opener):
            snapshot = feeds.fetch_snapshot("https://example.com/rss")
        self.assertEqual(
            ["World news", "Sport news", "More sport"],
            [entry["headline"] for entry in snapshot.entries],
        )
        self.assertEqual("Reuters", snapshot.entries[0]["credit"])
        self.assertEqual("https://example.com/world", snapshot.entries[0]["url"])
        self.assertFalse(snapshot.entries[0]["sport"])
        self.assertTrue(snapshot.entries[1]["sport"])
        self.assertTrue(snapshot.entries[2]["sport"])

    def test_compressed_feed_cannot_bypass_size_limit(self):
        opener = MagicMock()
        opener.open.return_value = FakeResponse(gzip.compress(RSS))
        with patch.object(feeds, "_public_url_opener", return_value=opener), \
             patch.object(feeds, "MAX_FEED_BYTES", 256):
            with self.assertRaisesRegex(ValueError, "larger than 4 MB"):
                feeds.fetch_snapshot("https://example.com/rss")

    def test_invalid_gzip_is_reported_as_fetch_error(self):
        corrupt_deflate = bytearray(gzip.compress(RSS))
        corrupt_deflate[10] = (corrupt_deflate[10] & 0xF9) | 0x06
        for body in (b"\x1f\x8bnot gzip", gzip.compress(RSS)[:-5], bytes(corrupt_deflate)):
            with self.subTest(body=body):
                opener = MagicMock()
                opener.open.return_value = FakeResponse(body)
                with patch.object(feeds, "_public_url_opener", return_value=opener):
                    with self.assertRaisesRegex(ValueError, "Could not fetch feed"):
                        feeds.fetch_snapshot("https://example.com/rss")

    def fetch(self, raw):
        opener = MagicMock()
        opener.open.return_value = FakeResponse(raw)
        with patch.object(feeds, "_public_url_opener", return_value=opener):
            return feeds.fetch_snapshot("https://news.un.org/feed/subscribe/en/news/all/rss.xml")

    def test_plain_xml_remains_supported(self):
        self.assertEqual("UN News", self.fetch(RSS).title)

    def test_gzip_expansion_is_bounded(self):
        raw = gzip.compress(b" " * (feeds.MAX_FEED_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "larger than 4 MB"):
            self.fetch(raw)

    def test_invalid_gzip_is_reported_as_a_feed_error(self):
        with self.assertRaisesRegex(ValueError, "Could not fetch feed"):
            self.fetch(b"\x1f\x8btruncated")

    def test_feed_metadata_and_sport_filter_reach_normalized_entries(self):
        raw = b"""<rss version="2.0"><channel><title>News</title>
        <item><title>Security Council meets on Sudan ceasefire</title>
        <link>https://example.com/world</link><author>Reuters</author></item>
        <item><title>Local bakery opens</title><link>https://example.com/local</link></item>
        <item><title>Football match</title><link>https://example.com/sport/1</link></item>
        </channel></rss>"""
        entries = self.fetch(raw).entries
        self.assertEqual(3, len(entries))
        self.assertEqual("Reuters", entries[0]["credit"])
        self.assertTrue(entries[0]["geopolitical"])
        self.assertFalse(entries[1]["geopolitical"])
        self.assertIn("not geopolitical", entries[1]["topic_reason"])
        self.assertTrue(entries[2]["sport"])
        self.assertFalse(entries[2]["geopolitical"])

    def test_plain_feed_is_still_parsed(self):
        opener = MagicMock()
        opener.open.return_value = FakeResponse(RSS)
        with patch.object(feeds, "_public_url_opener", return_value=opener):
            snapshot = feeds.fetch_snapshot("https://news.un.org/feed.xml")
        self.assertEqual("UN News", snapshot.title)
        self.assertEqual(1, len(snapshot.entries))

    def test_gzip_body_without_content_encoding_header_is_decoded(self):
        opener = MagicMock()
        opener.open.return_value = FakeResponse(gzip.compress(RSS))
        with patch.object(feeds, "_public_url_opener", return_value=opener):
            snapshot = feeds.fetch_snapshot("https://news.un.org/feed/subscribe/en/news/all/rss.xml")
        self.assertEqual(["Security Council meets on Sudan ceasefire"], [e["headline"] for e in snapshot.entries])
        self.assertEqual("Associated Press", snapshot.entries[0]["credit"])

    def test_gzip_expansion_over_feed_limit_is_rejected(self):
        opener = MagicMock()
        opener.open.return_value = FakeResponse(gzip.compress(b"x" * 4_000_001))
        with patch.object(feeds, "_public_url_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "larger than 4 MB"):
                feeds.fetch_snapshot("https://news.un.org/feed/subscribe/en/news/all/rss.xml")

    def test_decompressed_feed_size_is_limited(self):
        opener = MagicMock()
        opener.open.return_value = FakeResponse(gzip.compress(b"x" * 4_000_001))
        with patch.object(feeds, "_public_url_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "larger than 4 MB"):
                feeds.fetch_snapshot("https://news.un.org/feed.xml")

    def test_truncated_gzip_is_rejected(self):
        opener = MagicMock()
        opener.open.return_value = FakeResponse(gzip.compress(RSS)[:-8])
        with patch.object(feeds, "_public_url_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "Could not fetch feed"):
                feeds.fetch_snapshot("https://news.un.org/feed.xml")

    def test_wire_credit_and_sport_filter_are_applied_to_entries(self):
        raw = b"""<rss version="2.0"><channel><title>News</title>
        <item><title>World news</title><link>https://example.org/world/1</link>
        <author>Jane Doe, Reuters</author></item>
        <item><title>Match results</title><link>https://example.org/sport/2</link></item>
        <item><title>More results</title><link>https://example.org/news/3</link>
        <category>Sport</category></item></channel></rss>"""
        opener = MagicMock()
        opener.open.return_value = FakeResponse(raw)
        with patch.object(feeds, "_public_url_opener", return_value=opener):
            snapshot = feeds.fetch_snapshot("https://example.org/feed.xml")
        self.assertEqual(
            ["World news", "Match results", "More results"],
            [entry["headline"] for entry in snapshot.entries],
        )
        self.assertEqual("Reuters", snapshot.entries[0]["credit"])
        self.assertTrue(snapshot.entries[1]["sport"])
        self.assertTrue(snapshot.entries[2]["sport"])

    def test_feed_url_resolving_to_private_address_is_refused(self):
        private = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with patch.object(netsafe.socket, "getaddrinfo", return_value=private):
            with self.assertRaisesRegex(ValueError, "public IP"):
                feeds.fetch_snapshot("https://internal.example/feed.xml")


if __name__ == "__main__":
    unittest.main()
