"""Bluesky posting: article-preview embeds (falling back to the source logo), plus
login/session handling."""

from __future__ import annotations

import hashlib
import io
import os
import re
import threading
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from atproto import Client, client_utils, models
from atproto_client.exceptions import BadRequestError, LoginRequiredError, UnauthorizedError
from PIL import Image, ImageOps, UnidentifiedImageError

from feeds import canonical_story_key
from netsafe import (
    _connect_public_socket,
    _public_url_opener,
    _validate_public_http_url,
)

# Error names Bluesky uses when it is telling us the login itself is no good.
_SESSION_ERROR_NAMES = {"ExpiredToken", "InvalidToken", "AuthenticationRequired"}

USER_AGENT = "BskyNewsFeedBot/0.1 (+local RSS reader)"
ARTICLE_FETCH_TIMEOUT = 10
MAX_HTML_BYTES = 1_500_000
MAX_THUMB_DOWNLOAD_BYTES = 8_000_000
BLUESKY_IMAGE_CAP = 1_900_000  # keep just under Bluesky's current 2 MB image cap
MAX_THUMB_PIXELS = 20_000_000
_TID_ALPHABET = "234567abcdefghijklmnopqrstuvwxyz"
_TID_PATTERN = re.compile(r"^[2-7a-z]{13}$")


def attribution_line(source_name: str, wire_credit: str | None = None) -> str:
    attribution = f"Source: {source_name.strip()}"
    if wire_credit and wire_credit.strip().casefold() != source_name.strip().casefold():
        attribution += f", with {wire_credit.strip()}"
    return attribution


def _record_key(article_url: str, seen: str | None = None) -> str:
    digest = hashlib.sha256(canonical_story_key(article_url).encode("utf-8")).digest()
    timestamp = None
    if seen:
        try:
            parsed = datetime.fromisoformat(seen.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            timestamp = int(parsed.timestamp() * 1_000_000)
        except (OverflowError, OSError, ValueError):
            pass

    if timestamp is None:
        value = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
    else:
        timestamp &= (1 << 53) - 1
        clock_id = int.from_bytes(digest[:2], "big") & 0x3FF
        value = (timestamp << 10) | clock_id

    key = []
    for _ in range(13):
        key.append(_TID_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(key))


def _link_display_text(article_url: str) -> str:
    """Just the domain, e.g. "bbc.co.uk" - shown in the post text and clicked through
    to the real article. Long URLs (DW's especially) could otherwise burn 200+
    characters of the 300-character post limit on their own.
    """
    host = (urlsplit(article_url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or article_url  # fall back to the full url if it somehow has no host


class _OpenGraphParser(HTMLParser):
    """Pulls og:title / og:description / og:image out of an article page's <head>,
    with plain <title>/<meta name="description"> as a fallback. Stops at </head> -
    nothing we want ever appears later in the page, and it keeps this fast and
    tolerant of long/broken pages instead of parsing the whole body.
    """

    def __init__(self) -> None:
        super().__init__()
        self.og_title: str | None = None
        self.og_description: str | None = None
        self.og_image: str | None = None
        self.title_tag: str | None = None
        self.meta_description: str | None = None
        self._in_title_tag = False
        self.done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        if tag == "title":
            self._in_title_tag = True
        elif tag == "meta":
            values = {k.lower(): (v or "").strip() for k, v in attrs if v}
            prop = values.get("property", "").lower()
            name = values.get("name", "").lower()
            content = values.get("content", "")
            if not content:
                return
            if prop == "og:title":
                self.og_title = content
            elif prop == "og:description":
                self.og_description = content
            elif prop == "og:image":
                self.og_image = content
            elif name == "description" and not self.meta_description:
                self.meta_description = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title_tag = False
        elif tag == "head":
            self.done = True

    def handle_data(self, data: str) -> None:
        if self._in_title_tag and not self.title_tag:
            cleaned = data.strip()
            if cleaned:
                self.title_tag = cleaned


def _fetch_article_preview(article_url: str) -> tuple[str | None, str | None, bytes | None]:
    """Best-effort (title, description, raw thumbnail bytes) for an article page.
    Anything that can't be found or fetched comes back None for that piece - a
    slow, broken, or bot-blocking article page should never be the reason a story
    doesn't get posted, only the reason it posts with the source's logo instead.
    """
    try:
        request = urllib.request.Request(
            article_url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"}
        )
        _validate_public_http_url(article_url)
        with _public_url_opener().open(request, timeout=ARTICLE_FETCH_TIMEOUT) as response:
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower() and content_type:
                return None, None, None
            raw = response.read(MAX_HTML_BYTES)
            final_url = response.geturl()
            charset = response.headers.get_content_charset() or "utf-8"
    except Exception:
        return None, None, None

    try:
        html_text = raw.decode(charset, errors="replace")
    except (LookupError, UnicodeError):
        html_text = raw.decode("utf-8", errors="replace")

    parser = _OpenGraphParser()
    try:
        parser.feed(html_text)
    except Exception:
        pass

    title = parser.og_title or parser.title_tag
    description = parser.og_description or parser.meta_description

    thumb_bytes = None
    if parser.og_image:
        try:
            image_url = urljoin(final_url, parser.og_image)
            _validate_public_http_url(image_url)
            img_request = urllib.request.Request(image_url, headers={"User-Agent": USER_AGENT})
            with _public_url_opener().open(
                img_request, timeout=ARTICLE_FETCH_TIMEOUT
            ) as img_response:
                img_content_type = img_response.headers.get("Content-Type", "")
                if "image" in img_content_type.lower() or not img_content_type:
                    downloaded = img_response.read(MAX_THUMB_DOWNLOAD_BYTES + 1)
                    if len(downloaded) <= MAX_THUMB_DOWNLOAD_BYTES:
                        thumb_bytes = downloaded
        except Exception:
            thumb_bytes = None

    return title, description, thumb_bytes


def _compress_for_bluesky(raw: bytes) -> bytes | None:
    """Re-encode arbitrary image bytes to a WebP under Bluesky's image cap. Returns
    None if the bytes aren't a readable image, or can't be brought under the cap -
    the caller falls back (article thumbnail -> logo -> no image) rather than fail
    the whole post over it.
    """
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.width * opened.height > MAX_THUMB_PIXELS:
                return None
            image = ImageOps.exif_transpose(opened).copy()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError):
        return None

    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    image = image.convert("RGBA") if "A" in image.getbands() else image.convert("RGB")

    output = io.BytesIO()
    for quality in (94, 88, 82, 76, 68):
        output.seek(0)
        output.truncate(0)
        image.save(output, format="WEBP", quality=quality, method=6)
        if output.tell() <= BLUESKY_IMAGE_CAP:
            break
    if output.tell() > BLUESKY_IMAGE_CAP:
        image.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
        output.seek(0)
        output.truncate(0)
        image.save(output, format="WEBP", quality=70, method=6)
    if output.tell() > BLUESKY_IMAGE_CAP or output.tell() == 0:
        return None
    return output.getvalue()


def _is_session_error(exc: Exception) -> bool:
    """True when Bluesky is rejecting our login/session, as opposed to a network
    blip, a rate limit, or a problem with one particular post. Only in that case is
    throwing the cached login away and logging in fresh worth doing."""
    if isinstance(exc, (UnauthorizedError, LoginRequiredError)):
        return True
    if isinstance(exc, BadRequestError):
        content = getattr(getattr(exc, "response", None), "content", None)
        return getattr(content, "error", None) in _SESSION_ERROR_NAMES
    return False


class BlueskyPublisher:
    def __init__(self) -> None:
        self.handle = os.getenv("BLUESKY_HANDLE", "oninews.bsky.social").strip()
        self.app_password = os.getenv("BLUESKY_APP_PASSWORD", "").strip()
        self._client: Client | None = None
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.handle and self.app_password)

    def _get_client(self) -> Client:
        if not self.configured:
            raise RuntimeError(
                "Bluesky is not configured. Add a Bluesky app password to the local .env file."
            )
        with self._lock:
            if self._client is None:
                client = Client()
                client.login(self.handle, self.app_password)
                self._client = client
            return self._client

    def _forget_client(self) -> None:
        with self._lock:
            self._client = None

    def check_connection(self) -> str:
        client = self._get_client()
        try:
            # A real (cheap) authenticated request, so this genuinely verifies the
            # session instead of just confirming a login object exists in memory.
            client.com.atproto.server.get_session()
        except Exception:
            self._forget_client()
            raise
        return self.handle

    def post_story(
        self,
        *,
        headline: str,
        source_name: str,
        article_url: str,
        logo_path: Path | None,
        logo_alt: str,
        credit: str | None = None,
        first_seen_at: str | None = None,
    ) -> str:
        lead = f"{headline}\n\n({attribution_line(source_name, credit)})\n\n"
        link_text = _link_display_text(article_url)
        if len(lead) + len(link_text) > 300:
            raise ValueError(
                "The full headline, source line, and link exceed Bluesky's post-length limit. "
                "This story was left unposted rather than shortening the headline."
            )
        builder = client_utils.TextBuilder().text(lead).link(link_text, article_url)

        # Try the article's own preview image first; fall back to the source's logo;
        # fall back again to a text-only card if even that isn't available. Nothing
        # in this chain raises - a broken article page should degrade, not block posting.
        og_title, og_description, article_thumb_raw = _fetch_article_preview(article_url)

        thumb_bytes = _compress_for_bluesky(article_thumb_raw) if article_thumb_raw else None
        if thumb_bytes is None and logo_path and logo_path.is_file():
            try:
                thumb_bytes = _compress_for_bluesky(logo_path.read_bytes())
            except OSError:
                thumb_bytes = None

        embed_title = (og_title or headline).strip()[:300] or headline[:300]
        embed_description = (og_description or f"via {source_name}").strip()[:1000]

        client = self._get_client()
        record_key = _record_key(article_url, first_seen_at)
        try:
            external = models.AppBskyEmbedExternal.External(
                uri=article_url,
                title=embed_title,
                description=embed_description,
            )
            if thumb_bytes is not None:
                upload = client.upload_blob(thumb_bytes)
                external.thumb = upload.blob
            embed = models.AppBskyEmbedExternal.Main(external=external)
            record = models.AppBskyFeedPost.Record(
                created_at=client.get_current_time_iso(),
                text=builder.build_text(),
                embed=embed,
                facets=builder.build_facets(),
            )
            result = client.com.atproto.repo.create_record(
                models.ComAtprotoRepoCreateRecord.Data(
                    repo=self.handle,
                    collection="app.bsky.feed.post",
                    rkey=record_key,
                    record=record,
                )
            )
        except Exception as exc:
            if _is_session_error(exc):
                self._forget_client()
                raise
            try:
                existing = client.com.atproto.repo.get_record(
                    models.ComAtprotoRepoGetRecord.Params(
                        repo=self.handle,
                        collection="app.bsky.feed.post",
                        rkey=record_key,
                    )
                )
            except Exception:
                raise exc

            value = getattr(existing, "value", None)
            if isinstance(value, dict):
                embed_value = value.get("embed", {})
                external = embed_value.get("external", {}) if isinstance(embed_value, dict) else {}
                existing_url = external.get("uri") if isinstance(external, dict) else None
            else:
                embed_value = getattr(value, "embed", None)
                external = getattr(embed_value, "external", None)
                existing_url = getattr(external, "uri", None)
            if (
                existing_url
                and canonical_story_key(existing_url) == canonical_story_key(article_url)
            ):
                return str(existing.uri)
            if existing_url:
                raise RuntimeError(
                    "The existing record key belongs to a different article."
                ) from exc
            raise exc
        return str(result.uri)
