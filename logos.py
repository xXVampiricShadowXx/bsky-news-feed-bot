"""Source logo storage, website icon discovery, and a generated text fallback."""

from __future__ import annotations

import hashlib
import html
import io
import re
import urllib.request
import uuid
from urllib.parse import urljoin

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

import db
from config import USER_AGENT
from netsafe import _public_url_opener, _validate_public_http_url

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_BLUESKY_BYTES = 1_900_000
MIN_ICON_PIXELS = 96
_LINK_TAG = re.compile(r"<link\b[^>]*>", re.I)
_ATTR = r"""{}\s*=\s*["']([^"']+)["']"""


def store_logo_bytes(raw: bytes, source_name: str) -> tuple[str, str]:
    """Validate an image, re-encode it as WebP under Bluesky's limit, and save it."""
    if not raw:
        raise ValueError("The selected logo file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("Logo files must be 10 MB or smaller.")

    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Use a PNG, JPG, or WebP logo file.")
            image = ImageOps.exif_transpose(opened).copy()
    except (UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("That file is not a supported image. Use PNG, JPG, or WebP.") from exc

    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    image = image.convert("RGBA" if "A" in image.getbands() else "RGB")

    # Re-encoding strips camera metadata. Keep the result below Bluesky's 2 MB image cap.
    output = io.BytesIO()
    for quality in (94, 88, 82, 76, 68):
        output.seek(0)
        output.truncate(0)
        image.save(output, format="WEBP", quality=quality, method=6)
        if output.tell() <= MAX_BLUESKY_BYTES:
            break
    if output.tell() > MAX_BLUESKY_BYTES:
        image.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
        output.seek(0)
        output.truncate(0)
        image.save(output, format="WEBP", quality=70, method=6)
    if output.tell() > MAX_BLUESKY_BYTES:
        raise ValueError("The logo could not be reduced below the Bluesky image limit.")

    filename = f"source-{uuid.uuid4().hex}.webp"
    target = db.LOGO_DIR / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(output.getvalue())
    return filename, f"{source_name} logo"


def generated_logo(source_name: str, size: int = 400) -> bytes:
    """A plain initials tile, used when a publisher's own icon can't be fetched."""
    digest = hashlib.sha256(source_name.encode("utf-8")).digest()
    background = (40 + digest[0] % 120, 40 + digest[1] % 120, 60 + digest[2] % 120)
    words = [word for word in re.split(r"[\s\-]+", source_name) if word[:1].isalnum()]
    letters = "".join(word[0] for word in words[:3]).upper() or "?"
    image = Image.new("RGB", (size, size), background)
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=int(size * (0.42 if len(letters) < 3 else 0.32)))
    except TypeError:  # Pillow < 10.1 has no scalable default font
        font = ImageFont.load_default()
    draw.text((size / 2, size / 2), letters, fill=(255, 255, 255), font=font, anchor="mm")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _icon_candidates(page: str, base_url: str) -> list[str]:
    ranked: list[tuple[int, str]] = []
    for tag in _LINK_TAG.findall(page):
        rel = re.search(_ATTR.format("rel"), tag, re.I)
        href = re.search(_ATTR.format("href"), tag, re.I)
        if not rel or not href:
            continue
        rel_value = rel.group(1).lower()
        if "apple-touch-icon" not in rel_value and rel_value not in {"icon", "shortcut icon"}:
            continue
        url = urljoin(base_url, html.unescape(href.group(1).strip()))
        if url.lower().split("?", 1)[0].endswith(".svg"):
            continue
        sizes = re.search(r"""sizes\s*=\s*["'](\d+)x""", tag, re.I)
        score = (1000 if "apple-touch-icon" in rel_value else 0) + (int(sizes.group(1)) if sizes else 0)
        ranked.append((score, url))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    urls = [url for _, url in ranked]
    fallback = urljoin(base_url, "/apple-touch-icon.png")
    if fallback not in urls:
        urls.append(fallback)
    return urls


def _download(opener, url: str, limit: int) -> tuple[bytes, str]:

    _validate_public_http_url(url)
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    )
    with opener.open(request, timeout=15) as response:
        return response.read(limit + 1)[:limit], response.geturl()


def discover_logo(website: str) -> bytes | None:
    """Return the best square icon a publisher advertises for its own site, if any."""

    opener = _public_url_opener()
    try:
        page, final_url = _download(opener, website, 2_000_000)
    except Exception:
        return None
    for url in _icon_candidates(page.decode("utf-8", "replace"), final_url)[:6]:
        try:
            raw, _ = _download(opener, url, MAX_UPLOAD_BYTES)
            with Image.open(io.BytesIO(raw)) as icon:
                if icon.format not in {"PNG", "JPEG", "WEBP"} or min(icon.size) < MIN_ICON_PIXELS:
                    continue
            return raw
        except Exception:
            continue
    return None
