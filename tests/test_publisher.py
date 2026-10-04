import io
import unittest
from email.message import Message
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

import netsafe
import publisher


class PreviewTests(unittest.TestCase):
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

    def test_decompression_bomb_warning_as_error_degrades_gracefully(self):
        with patch.object(
            publisher.Image, "open", side_effect=Image.DecompressionBombWarning("too large")
        ):
            self.assertIsNone(publisher._compress_for_bluesky(b"not an image"))

    def test_pixel_limit_is_checked_before_decoding(self):
        opened = MagicMock(width=11, height=10)
        opened.__enter__.return_value = opened
        with patch.object(publisher.Image, "open", return_value=opened), \
             patch.object(publisher, "MAX_THUMB_PIXELS", 100), \
             patch.object(publisher.ImageOps, "exif_transpose") as decode:
            self.assertIsNone(publisher._compress_for_bluesky(b"image"))
        decode.assert_not_called()

    def test_preview_uses_public_opener_for_article_and_image(self):
        headers = Message()
        headers["Content-Type"] = "text/html; charset=utf-8"
        response = MagicMock(headers=headers)
        response.read.return_value = b'<head><meta property="og:image" content="/image.png"></head>'
        response.geturl.return_value = "https://example.com/article"
        response.__enter__.return_value = response
        image = MagicMock(headers={"Content-Type": "image/png"})
        image.read.return_value = b"thumbnail"
        image.__enter__.return_value = image
        opener = MagicMock()
        opener.open.side_effect = [response, image]
        with patch.object(publisher, "_public_url_opener", return_value=opener), \
             patch.object(publisher, "_validate_public_http_url") as validate, \
             patch.object(publisher.urllib.request, "urlopen") as unsafe_open:
            self.assertEqual(
                (None, None, b"thumbnail"),
                publisher._fetch_article_preview("https://example.com/article"),
            )
        self.assertEqual(
            ["https://example.com/article", "https://example.com/image.png"],
            [call.args[0] for call in validate.call_args_list],
        )
        self.assertEqual(2, opener.open.call_count)
        unsafe_open.assert_not_called()

    def test_private_article_preview_never_opens_connection(self):
        with patch.object(publisher, "_public_url_opener") as opener:
            self.assertEqual(
                (None, None, None),
                publisher._fetch_article_preview("http://127.0.0.1/article"),
            )
        opener.assert_not_called()

    def test_private_thumbnail_keeps_metadata_without_fetching_image(self):
        headers = Message()
        headers["Content-Type"] = "text/html"
        response = MagicMock(headers=headers)
        response.read.return_value = (
            b'<head><title>News</title>'
            b'<meta property="og:image" content="http://127.0.0.1/image.png"></head>'
        )
        response.geturl.return_value = "https://example.com/article"
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        validate = publisher._validate_public_http_url
        with patch.object(publisher, "_public_url_opener", return_value=opener), \
             patch.object(
                 publisher, "_validate_public_http_url",
                 side_effect=lambda url: validate(url) if "127.0.0.1" in url else None,
             ):
            self.assertEqual(
                ("News", None, None),
                publisher._fetch_article_preview("https://example.com/article"),
            )
        self.assertEqual(1, opener.open.call_count)


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

    def test_worker_metadata_is_used_in_post(self):
        submitted = []
        self.client.com.atproto.repo.create_record = lambda data: (
            submitted.append(data) or SimpleNamespace(uri="at://did:plc:bot/app.bsky.feed.post/key")
        )
        seen = "2026-10-02T03:43:29+00:00"
        self.post(credit="Reuters", first_seen_at=seen)
        self.assertEqual(publisher._record_key(self.article, seen), submitted[0].rkey)
        self.assertIn("(Source: Example, with Reuters)", submitted[0].record.text)
        self.assertEqual("app.bsky.feed.post", submitted[0].collection)
        self.assertEqual(self.poster.handle, submitted[0].repo)
        self.assertEqual("2026-10-01T12:00:00Z", submitted[0].record.created_at)

    def test_record_keys_normalize_timezones_and_invalid_dates(self):
        self.assertEqual(
            publisher._record_key(self.article, "2026-10-02T03:43:29Z"),
            publisher._record_key(self.article, "2026-10-02T05:43:29+02:00"),
        )
        self.assertEqual(
            publisher._record_key(self.article, "2026-10-02T03:43:29"),
            publisher._record_key(self.article, "2026-10-02T03:43:29Z"),
        )
        self.assertEqual(
            publisher._record_key(self.article),
            publisher._record_key(self.article, "not a date"),
        )

    def test_credit_is_counted_in_length_limit_before_posting(self):
        with patch.object(self.poster, "_get_client") as client:
            with self.assertRaisesRegex(ValueError, "post-length limit"):
                self.post(credit="R" * 300)
        client.assert_not_called()

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

    def test_recovery_queries_the_same_repository_and_key(self):
        error = TimeoutError("response lost")
        create = MagicMock(side_effect=error)
        get = MagicMock(return_value=None)
        self.client.com.atproto.repo.create_record = create
        self.client.com.atproto.repo.get_record = get
        with patch.object(self.poster, "_forget_client") as forget:
            with self.assertRaises(TimeoutError) as raised:
                self.post()
        self.assertIs(error, raised.exception)
        forget.assert_not_called()
        data, params = create.call_args.args[0], get.call_args.args[0]
        self.assertEqual((data.repo, data.collection, data.rkey), (params.repo, params.collection, params.rkey))

    def test_session_rejection_forgets_client_before_retry(self):
        error = publisher.LoginRequiredError("login expired")
        self.client.com.atproto.repo.create_record = MagicMock(side_effect=error)
        get = MagicMock()
        self.client.com.atproto.repo.get_record = get
        with patch.object(self.poster, "_forget_client") as forget:
            with self.assertRaises(publisher.LoginRequiredError):
                self.post()
        forget.assert_called_once_with()
        get.assert_not_called()

    def test_recovery_session_rejection_preserves_original_error_and_forgets_client(self):
        error = TimeoutError("response lost")
        self.client.com.atproto.repo.create_record = MagicMock(side_effect=error)
        self.client.com.atproto.repo.get_record = MagicMock(
            side_effect=publisher.LoginRequiredError("login expired")
        )
        with patch.object(self.poster, "_forget_client") as forget:
            with self.assertRaises(TimeoutError) as raised:
                self.post()
        self.assertIs(error, raised.exception)
        forget.assert_called_once_with()

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

    def test_recovery_accepts_sdk_record_model(self):
        self.client.com.atproto.repo.create_record = lambda data: (_ for _ in ()).throw(
            TimeoutError("response lost")
        )
        self.client.com.atproto.repo.get_record = lambda params: SimpleNamespace(
            value=publisher.models.AppBskyFeedPost.Record(
                text="News",
                created_at=self.client.get_current_time_iso(),
                embed=publisher.models.AppBskyEmbedExternal.Main(
                    external=publisher.models.AppBskyEmbedExternal.External(
                        uri=self.article, title="News", description="Example",
                    )
                ),
            ),
            uri="at://did:plc:bot/app.bsky.feed.post/recovered",
        )
        self.assertEqual("at://did:plc:bot/app.bsky.feed.post/recovered", self.post())

    def test_record_without_article_is_not_recovered(self):
        self.client.com.atproto.repo.create_record = lambda data: (_ for _ in ()).throw(
            TimeoutError("response lost")
        )
        self.client.com.atproto.repo.get_record = lambda params: SimpleNamespace(
            value={"text": "Unrelated post"}, uri="at://did:plc:bot/app.bsky.feed.post/key",
        )
        with self.assertRaisesRegex(RuntimeError, "different article"):
            self.post()


if __name__ == "__main__":
    unittest.main()
