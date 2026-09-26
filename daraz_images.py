"""
Product thumbnails for the Daraz MCP server.
===========================================

MCP can carry images, not just text, so a search can show what a product
actually looks like instead of describing it. That costs real context, though —
every image is base64 in the model's window — so this module works hard to keep
each thumbnail small, and the server only calls it when asked.

Two things keep the cost down:

* **Daraz resizes for us.** Its CDN takes a size suffix, so we ask for a ~300px
  thumbnail rather than downloading a 1000px original and shrinking it here.
  No Pillow, no decode step. If the resized URL misbehaves we fall back to the
  original.
* **A hard budget.** `MAX_IMAGE_BYTES` per image and `MAX_IMAGES` per reply,
  both enforced after download. Anything over budget is dropped rather than
  sent, and the caller is told which ones are missing.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger("daraz-np.images")

# Daraz's CDN encodes the transform in the filename, e.g.
#   .../p/abc123.jpg           ->  the original, often 800-1200px
#   .../p/abc123.jpg_300x300q80.jpg_.webp  ->  a 300px webp thumbnail
THUMB_SUFFIX = "_{size}x{size}q{quality}.jpg_.webp"
_ALREADY_SIZED = re.compile(r"_\d+x\d+q?\d*\.jpg", re.I)

THUMB_SIZE = int(os.getenv("DARAZ_THUMB_SIZE", "300"))
THUMB_QUALITY = int(os.getenv("DARAZ_THUMB_QUALITY", "80"))
MAX_IMAGES = int(os.getenv("DARAZ_MAX_IMAGES", "8"))
MAX_IMAGE_BYTES = int(os.getenv("DARAZ_MAX_IMAGE_BYTES", str(400 * 1024)))
IMAGE_TIMEOUT = float(os.getenv("DARAZ_IMAGE_TIMEOUT", "10"))

# Formats Claude can actually read. Anything else is dropped.
ALLOWED_TYPES = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def thumbnail_url(url: str, size: int = THUMB_SIZE, quality: int = THUMB_QUALITY) -> str:
    """
    Rewrite a Daraz image URL to ask the CDN for a small thumbnail.

    Leaves non-Daraz hosts and already-sized URLs alone, so calling this twice
    is harmless.
    """
    if not url or "daraz" not in url.lower():
        return url or ""
    if _ALREADY_SIZED.search(url):
        return url
    if not re.search(r"\.(jpg|jpeg|png|webp)$", url, re.I):
        return url
    base = re.sub(r"\.(jpeg|png|webp)$", ".jpg", url, flags=re.I)
    return base + THUMB_SUFFIX.format(size=size, quality=quality)


def _download(session: requests.Session, url: str) -> Optional[Tuple[bytes, str]]:
    """Fetch one image. Returns (bytes, format) or None if unusable."""
    try:
        response = session.get(url, timeout=IMAGE_TIMEOUT, stream=True)
        if response.status_code != 200:
            logger.debug("Image HTTP %s for %s", response.status_code, url)
            return None
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        image_format = ALLOWED_TYPES.get(content_type)
        if not image_format:
            logger.debug("Unsupported image type %r for %s", content_type, url)
            return None

        # Stop reading as soon as we know it's too big, rather than buffering
        # a multi-megabyte original just to throw it away.
        chunks: List[bytes] = []
        total = 0
        for chunk in response.iter_content(8192):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                logger.debug("Image over budget (%s bytes) - dropping %s", total, url)
                response.close()
                return None
        data = b"".join(chunks)
        return (data, image_format) if data else None
    except Exception as exc:  # noqa: BLE001 - a missing picture must never fail a search
        logger.debug("Image fetch failed for %s: %s", url, exc)
        return None


def fetch_thumbnail(
    session: requests.Session, url: str
) -> Optional[Tuple[bytes, str]]:
    """The small version of one product image, falling back to the original."""
    if not url:
        return None
    small = thumbnail_url(url)
    if small != url:
        result = _download(session, small)
        if result:
            return result
        logger.debug("Thumbnail URL failed, retrying the original: %s", url)
    return _download(session, url)


def fetch_thumbnails(
    session: requests.Session,
    urls: List[str],
    limit: int = MAX_IMAGES,
) -> Dict[str, Tuple[bytes, str]]:
    """
    Download up to `limit` thumbnails, keyed by the original URL.

    Failures are simply absent from the result — a product whose picture won't
    load still gets its text entry.
    """
    limit = max(0, min(int(limit), MAX_IMAGES))
    out: Dict[str, Tuple[bytes, str]] = {}
    for url in urls:
        if len(out) >= limit:
            break
        if not url or url in out:
            continue
        result = fetch_thumbnail(session, url)
        if result:
            out[url] = result
    logger.info("Fetched %s/%s thumbnails", len(out), min(len(urls), limit))
    return out


def to_image_block(data: bytes, image_format: str) -> Any:
    """Wrap raw bytes as the MCP image content FastMCP knows how to serialise."""
    from fastmcp.utilities.types import Image  # noqa: PLC0415 - keeps this module importable without fastmcp

    return Image(data=data, format=image_format)
