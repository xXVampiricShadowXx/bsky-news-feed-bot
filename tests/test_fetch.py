import gzip
import io
import unittest
from unittest.mock import MagicMock, patch

import feeds
import netsafe

RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>UN News</title><link>https://news.un.org/en/</link>
<item><title>Security Council meets on Sudan ceasefire</title>
<link>https://news.un.org/en/story/2026/10/1</link></item>
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
            with self.assertRaisesRegex(ValueError, "invalid gzip"):
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
        self.assertEqual(["World news"], [entry["headline"] for entry in snapshot.entries])
        self.assertEqual("Reuters", snapshot.entries[0]["credit"])

    def test_feed_url_resolving_to_private_address_is_refused(self):
        private = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with patch.object(netsafe.socket, "getaddrinfo", return_value=private):
            with self.assertRaisesRegex(ValueError, "public IP"):
                feeds.fetch_snapshot("https://internal.example/feed.xml")


if __name__ == "__main__":
    unittest.main()
