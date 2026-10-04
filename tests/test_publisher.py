import io
import unittest
from email.message import Message
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

import netsafe
import publisher


class PreviewTests(unittest.TestCase):
    def test_article_and_thumbnail_use_public_only_opener(self):
        article = MagicMock()
        article.__enter__.return_value = article
        article.headers = Message()
        article.headers["Content-Type"] = "text/html; charset=utf-8"
        article.read.return_value = b'<head><meta property="og:image" content="/image.png"></head>'
        article.geturl.return_value = "https://example.com/story"
        image = MagicMock()
        image.__enter__.return_value = image
        image.headers = {"Content-Type": "image/png"}
        image.read.return_value = b"image"
        opener = MagicMock()
        opener.open.side_effect = [article, image]
        with patch.object(publisher, "_public_url_opener", return_value=opener), \
             patch.object(publisher, "_validate_public_http_url") as validate, \
             patch.object(publisher.urllib.request, "urlopen") as unsafe:
            self.assertEqual((None, None, b"image"), publisher._fetch_article_preview("https://example.com/story"))
        self.assertEqual(
            ["https://example.com/story", "https://example.com/image.png"],
            [call.args[0] for call in validate.call_args_list],
        )
        self.assertEqual(2, opener.open.call_count)
        unsafe.assert_not_called()

    def test_private_preview_degrades_without_opening_url(self):
        with patch.object(publisher, "_public_url_opener") as opener:
            self.assertEqual((None, None, None), publisher._fetch_article_preview("http://127.0.0.1/story"))
        opener.assert_not_called()

    def test_private_or_non_http_destinations_are_rejected(self):
        for url in ("http://127.0.0.1/x", "http://10.0.0.1/x", "file:///etc/passwd"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                publisher._validate_public_http_url(url)

    def test_rebound_private_address_is_rejected_before_socket_creation(self):
        public = [(2, 1, 6, "", ("93.184.216.34", 443))]
        private = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with patch.object(netsafe.socket, "getaddrinfo", side_effect=[public, private]), \
             patch.object(netsafe.socket, "socket") as create_socket:
            publisher._validate_public_http_url("https://example.com/image.jpg")
            with self.assertRaises(ValueError):
                publisher._connect_public_socket("example.com", 443, 1, None)
            create_socket.assert_not_called()

    def test_oversized_image_degrades_to_logo_fallback(self):
        image = Image.new("RGB", (16, 16), "red")
        raw = io.BytesIO()
        image.save(raw, format="PNG")
        with patch.object(publisher, "MAX_THUMB_PIXELS", 100):
            self.assertIsNone(publisher._compress_for_bluesky(raw.getvalue()))
        compressed = publisher._compress_for_bluesky(raw.getvalue())
        self.assertIsNotNone(compressed)
        with Image.open(io.BytesIO(compressed)) as opened:
            self.assertEqual("WEBP", opened.format)

    def test_pillow_decompression_bomb_degrades_gracefully(self):
        with patch.object(
            publisher.Image, "open", side_effect=Image.DecompressionBombError("too large")
        ):
            self.assertIsNone(publisher._compress_for_bluesky(b"not an image"))


class PostingTests(unittest.TestCase):
    def setUp(self):
        self.poster = publisher.BlueskyPublisher()
        self.article = "https://example.com/story?utm_source=rss"
        self.client = SimpleNamespace(
            get_current_time_iso=lambda: "2026-10-01T12:00:00Z",
            com=SimpleNamespace(
                atproto=SimpleNamespace(
                    repo=SimpleNamespace(
                        create_record=lambda data: SimpleNamespace(uri="at://did:plc:bot/app.bsky.feed.post/key"),
                        get_record=lambda params: None,
                    )
                )
            ),
        )

    def post(self, **kwargs):
        with patch.object(self.poster, "_get_client", return_value=self.client), \
             patch.object(publisher, "_fetch_article_preview", return_value=(None, None, None)):
            return self.poster.post_story(
                headline="News", source_name="Example", article_url=self.article,
                logo_path=None, logo_alt="Example logo",
                **kwargs,
            )

    def test_stable_record_key_and_rich_link(self):
        submitted = []
        self.client.com.atproto.repo.create_record = lambda data: (
            submitted.append(data) or SimpleNamespace(uri="at://did:plc:bot/app.bsky.feed.post/key")
        )
        self.assertEqual(self.post(), self.post())
        self.assertEqual(2, len(submitted))
        self.assertEqual(submitted[0].rkey, submitted[1].rkey)
        self.assertEqual(submitted[0].record.facets[0].features[0].uri, self.article)
        self.assertEqual(
            publisher._record_key(self.article),
            publisher._record_key("http://www.example.com/story"),
        )

    def test_record_key_is_a_valid_tid(self):
        seen = "2026-10-02T03:43:29+00:00"
        key = publisher._record_key(self.article, seen)
        self.assertRegex(key, publisher._TID_PATTERN)
        self.assertEqual(key, publisher._record_key("http://www.example.com/story", seen))
        self.assertNotEqual(key, publisher._record_key("https://example.com/other", seen))
        self.assertRegex(publisher._record_key(self.article), publisher._TID_PATTERN)
        self.assertRegex(publisher._record_key(self.article, "not a date"), publisher._TID_PATTERN)
        try:
            from atproto_client.models.string_formats import validate_tid
        except ImportError:
            return
        validate_tid(key, None)

    def test_worker_attribution_and_first_seen_are_supported(self):
        submitted = []
        self.client.com.atproto.repo.create_record = lambda data: (
            submitted.append(data) or SimpleNamespace(uri="at://did:plc:bot/app.bsky.feed.post/key")
        )
        seen = "2026-10-02T03:43:29+00:00"
        self.post(credit="Reuters", first_seen_at=seen)
        self.assertIn("(Source: Example, with Reuters)", submitted[0].record.text)
        self.assertEqual(publisher._record_key(self.article, seen), submitted[0].rkey)
        self.assertEqual(
            submitted[0].rkey, publisher._record_key(self.article, "2026-10-02T03:43:29Z")
        )

    def test_many_stories_seen_together_have_distinct_record_keys(self):
        keys = {publisher._record_key(f"https://example.com/{i}", "2026-10-02T03:43:29Z")
                for i in range(2000)}
        self.assertEqual(2000, len(keys))

    def test_typed_existing_record_is_recovered(self):
        self.client.com.atproto.repo.create_record = lambda data: (_ for _ in ()).throw(
            TimeoutError("response lost")
        )
        value = publisher.models.AppBskyFeedPost.Record(
            text="News", created_at=self.client.get_current_time_iso(),
            embed=publisher.models.AppBskyEmbedExternal.Main(
                external=publisher.models.AppBskyEmbedExternal.External(
                    uri=self.article, title="News", description="",
                )
            ),
        )
        self.client.com.atproto.repo.get_record = lambda params: SimpleNamespace(
            value=value, uri="at://did:plc:bot/app.bsky.feed.post/recovered",
        )
        self.assertEqual("at://did:plc:bot/app.bsky.feed.post/recovered", self.post())

    def test_lost_response_recovers_existing_post(self):
        self.client.com.atproto.repo.create_record = lambda data: (_ for _ in ()).throw(
            TimeoutError("response lost")
        )
        self.client.com.atproto.repo.get_record = lambda params: SimpleNamespace(
            value={"embed": {"external": {"uri": self.article}}},
            uri="at://did:plc:bot/app.bsky.feed.post/recovered",
        )
        self.assertEqual(
            "at://did:plc:bot/app.bsky.feed.post/recovered", self.post()
        )

    def test_existing_post_with_equivalent_tracking_url_is_recovered(self):
        self.client.com.atproto.repo.create_record = lambda data: (_ for _ in ()).throw(
            RuntimeError("record exists")
        )
        self.client.com.atproto.repo.get_record = lambda params: SimpleNamespace(
            value={"embed": {"external": {"uri": "http://www.example.com/story"}}},
            uri="at://did:plc:bot/app.bsky.feed.post/recovered",
        )
        self.assertEqual(
            "at://did:plc:bot/app.bsky.feed.post/recovered", self.post()
        )

    def test_missing_post_propagates_original_error_for_retry(self):
        self.client.com.atproto.repo.create_record = lambda data: (_ for _ in ()).throw(
            TimeoutError("response lost")
        )
        self.client.com.atproto.repo.get_record = lambda params: (_ for _ in ()).throw(
            RuntimeError("record not found")
        )
        with self.assertRaisesRegex(TimeoutError, "response lost"):
            self.post()

    def test_conflicting_record_is_not_marked_posted(self):
        self.client.com.atproto.repo.create_record = lambda data: (_ for _ in ()).throw(
            RuntimeError("record exists")
        )
        self.client.com.atproto.repo.get_record = lambda params: SimpleNamespace(
            value={"embed": {"external": {"uri": "https://example.com/other"}}},
            uri="at://did:plc:bot/app.bsky.feed.post/collision",
        )
        with self.assertRaisesRegex(RuntimeError, "different article"):
            self.post()


if __name__ == "__main__":
    unittest.main()
