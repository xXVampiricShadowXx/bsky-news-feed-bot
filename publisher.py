"""Bluesky posting: article-preview embeds (falling back to the source logo), plus
login/session handling."""

from __future__ import annotations

import io
import http.client
import hashlib
import ipaddress
import os
import re
import socket
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

# Error names Bluesky uses when it is telling us the login itself is no good.
_SESSION_ERROR_NAMES = {"ExpiredToken", "InvalidToken", "AuthenticationRequired"}

USER_AGENT = "OniNewsFeedBot/0.1 (+local RSS reader)"
ARTICLE_FETCH_TIMEOUT = 10
MAX_HTML_BYTES = 1_500_000
MAX_THUMB_DOWNLOAD_BYTES = 8_000_000
MAX_THUMB_PIXELS = 20_000_000
BLUESKY_IMAGE_CAP = 1_900_000  # keep just under Bluesky's current 2 MB image cap


def _public_address_info(host: str, port: int | None) -> list[tuple]:
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Could not resolve URL hostname.") from exc
    if not results or any(not ipaddress.ip_address(result[4][0]).is_global for result in results):
        raise ValueError("URL must resolve only to public IP addresses.")
    return results


def _connect_public_socket(
    host: str, port: int | None, timeout: float | None, source_address: tuple | None
) -> socket.socket:
    last_error = None
    for family, socktype, proto, _canonname, sockaddr in _public_address_info(host, port):
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    if last_error:
        raise last_error
    raise ValueError("URL hostname has no public addresses.")


class _PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        if self._tunnel_host:
            raise ValueError("HTTP tunnels are not allowed.")
        self.sock = _connect_public_socket(
            self.host, self.port, self.timeout, self.source_address
        )


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        if self._tunnel_host:
            raise ValueError("HTTPS tunnels are not allowed.")
        sock = _connect_public_socket(self.host, self.port, self.timeout, self.source_address)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def _validate_public_http_url(url: str) -> None:
    """Reject non-HTTP(S), credential-bearing, and non-public destinations."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use HTTP or HTTPS and include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are not allowed.")
    _public_address_info(parsed.hostname, parsed.port)


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTPConnection, req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


class _PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _public_url_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _PublicHTTPHandler(),
        _PublicHTTPSHandler(),
        _PublicOnlyRedirectHandler(),
    )


def attribution_line(source_name: str, credit: str | None = None) -> str:
    """"Source: PBS News, with Associated Press" - names the publisher and, when the
    feed's byline says so, the wire service that actually wrote the story."""
    line = f"Source: {source_name}"
    if credit and credit.casefold() not in source_name.casefold():
        line += f", with {credit}"
    return line


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
        _validate_public_http_url(article_url)
        request = urllib.request.Request(
            article_url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"}
        )
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
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None


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


_TID_ALPHABET = "234567abcdefghijklmnopqrstuvwxyz"
_TID_PATTERN = re.compile(r"^[234567abcdefghij][234567abcdefghijklmnopqrstuvwxyz]{12}$")


def _record_key(article_url: str, first_seen_at: str | None = None) -> str:
    """A valid, deterministic TID record key for this story.

    Bluesky only accepts TIDs (13-char base32 timestamp ids) as post record keys.
    The timestamp comes from when the bot first saw the story, and the sub-second
    part plus clock id come from the story's URL hash, so every retry of the same
    story reuses the same key - letting a retry find, not duplicate, an earlier post.
    """
    digest = int.from_bytes(
        hashlib.sha256(canonical_story_key(article_url).encode("utf-8")).digest()[:8], "big"
    )
    seconds = 0
    if first_seen_at:
        try:
            stamp = datetime.fromisoformat(first_seen_at.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            seconds = int(stamp.timestamp())
        except ValueError:
            seconds = 0
    if seconds <= 0:
        seconds = 1_600_000_000 + digest % 100_000_000  # stable fallback, Sept 2020 - Dec 2023
    micros = seconds * 1_000_000 + digest % 1_000_000
    clock_id = (digest >> 20) & 0x3FF
    value = ((micros & ((1 << 53) - 1)) << 10) | clock_id
    chars = []
    for _ in range(13):
        chars.append(_TID_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def _posted_article_url(value: object) -> str | None:
    embed = value.get("embed") if isinstance(value, dict) else getattr(value, "embed", None)
    external = embed.get("external") if isinstance(embed, dict) else getattr(embed, "external", None)
    return external.get("uri") if isinstance(external, dict) else getattr(external, "uri", None)


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
        text = client_utils.TextBuilder().text(lead).link(link_text, article_url)

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
        embed_description = (og_description or attribution_line(source_name, credit)).strip()[:1000]

        client = self._get_client()
        rkey = _record_key(article_url, first_seen_at)
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
                text=text.build_text(),
                facets=text.build_facets(),
                embed=embed,
                langs=["en"],
            )
            result = client.com.atproto.repo.create_record(
                models.ComAtprotoRepoCreateRecord.Data(
                    repo=self.handle,
                    collection="app.bsky.feed.post",
                    rkey=rkey,
                    record=record,
                )
            )
        except Exception as exc:
            if _is_session_error(exc):
                self._forget_client()
            else:
                # A timeout can mean the record was created but the response was lost.
                # The stable rkey prevents a retry from creating a second post.
                try:
                    existing = client.com.atproto.repo.get_record(
                        models.ComAtprotoRepoGetRecord.Params(
                            repo=self.handle, collection="app.bsky.feed.post", rkey=rkey
                        )
                    )
                except Exception:
                    pass
                else:
                    posted_url = _posted_article_url(existing.value)
                    if posted_url and canonical_story_key(posted_url) == canonical_story_key(article_url):
                        return str(existing.uri)
                    raise RuntimeError("Bluesky record key belongs to a different article.") from exc
            raise
        return str(result.uri)
