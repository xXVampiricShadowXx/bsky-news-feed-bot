import gzip
import io
import unittest
from unittest.mock import patch

import feeds

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
    def test_gzip_body_without_content_encoding_header_is_decoded(self):
        with patch.object(feeds.urllib.request, "urlopen", return_value=FakeResponse(gzip.compress(RSS))):
            snapshot = feeds.fetch_snapshot("https://news.un.org/feed/subscribe/en/news/all/rss.xml")
        self.assertEqual(["Security Council meets on Sudan ceasefire"], [e["headline"] for e in snapshot.entries])


if __name__ == "__main__":
    unittest.main()
