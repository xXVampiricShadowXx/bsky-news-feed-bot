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

    def test_feed_url_resolving_to_private_address_is_refused(self):
        private = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with patch.object(netsafe.socket, "getaddrinfo", return_value=private):
            with self.assertRaisesRegex(ValueError, "public IP"):
                feeds.fetch_snapshot("https://internal.example/feed.xml")


if __name__ == "__main__":
    unittest.main()
