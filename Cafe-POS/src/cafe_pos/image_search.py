from __future__ import annotations

from collections import deque
import hashlib
import io
import ipaddress
import json
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtCore import QObject, QThread, Signal

from .bilingual import suggest_bilingual_name
from .config import MAX_PRODUCT_IMAGE_BYTES, SUPPORTED_PRODUCT_IMAGE_EXTS, validate_image_provider
from .i18n import tr
from .paths import get_product_images_dir

USER_AGENT = "CafePOS-Desktop/2.0 (+https://github.com/MohameddTamerr/POS-Systems-Collection)"
ALLOWED_OPENVERSE_LICENSES = {"cc0", "pdm"}
SEARCH_TIMEOUT_SECONDS = 12.0
DOWNLOAD_TIMEOUT_SECONDS = 15.0
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024  # 2 MB limit per gallery thumbnail
OUTPUT_IMAGE_SIZE = 512


class ImageSearchError(Exception):
    """Structured error raised by product image providers and downloaders."""

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code  # 'offline', 'timeout', 'rate_limit', 'auth', 'empty', 'invalid', 'cancelled', 'error'


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True)
class ImageSearchResult:
    id: str
    title: str
    thumbnail_url: str
    image_url: str
    source_provider: str
    creator: str
    creator_url: str
    license_code: str
    license_url: str
    attribution: str
    landing_url: str
    width: int = 0
    height: int = 0

    @property
    def badge_label(self) -> str:
        code = (self.license_code or "").upper()
        if code in {"CC0", "PDM"}:
            return code
        if self.source_provider == "pexels":
            return "Pexels"
        return code or "Open"


class ImageSearchProvider(Protocol):
    name: str

    def search(
        self, query: str, limit: int = 8, cancel_token: CancellationToken | None = None
    ) -> list[ImageSearchResult]:
        ...


_SIZE_NOISE_RE = re.compile(
    r"\(\s*(?:\d+\s*(?:g|kg|ml|l|oz|lb|pcs?)|small|medium|large|regular)\s*\)"
    r"|\b(?:small|medium|large|regular|iced coffee shop)\b"
    r"|(?:صغير|وسط|متوسط|كبير|عادي)",
    re.IGNORECASE,
)

_AR_CHAR_RE = re.compile(r"[\u0600-\u06FF]")

_AR_CAFE_PHRASES: tuple[tuple[str, str], ...] = (
    ("فلات وايت", "flat white"),
    ("دبل اسبريسو", "double espresso"),
    ("دبل إسبريسو", "double espresso"),
    ("سبانيش لاتيه مثلج", "iced spanish latte"),
    ("سبانيش لاتيه", "spanish latte"),
    ("لاتيه مثلج", "iced latte"),
    ("امريكانو مثلج", "iced americano"),
    ("أمريكانو مثلج", "iced americano"),
    ("موكا مثلجة", "iced mocha"),
    ("ماتشا مثلج", "iced matcha"),
    ("ماتشا مثلجة", "iced matcha"),
    ("شاي ماتشا", "matcha latte"),
    ("قهوة تركي", "turkish coffee"),
    ("تشيز كيك", "cheesecake"),
    ("شاي باللبن", "milk tea"),
    ("شاي أخضر", "green tea"),
    ("شاي اخضر", "green tea"),
    ("شاي بالنعناع", "mint tea"),
    ("حبوب قهوة", "roasted coffee beans"),
    ("حبوب القهوة", "roasted coffee beans"),
    ("ايس كوفي", "iced coffee"),
    ("آيس كوفي", "iced coffee"),
    ("كولد برو", "cold brew coffee"),
    ("كاراميل ماكياتو", "caramel macchiato"),
    ("كراميل ماكياتو", "caramel macchiato"),
)

_AR_CAFE_WORDS: dict[str, str] = {
    "اسبريسو": "espresso",
    "إسبريسو": "espresso",
    "لاتيه": "latte",
    "كابتشينو": "cappuccino",
    "امريكانو": "americano",
    "أمريكانو": "americano",
    "موكا": "mocha",
    "ماكياتو": "macchiato",
    "كورتادو": "cortado",
    "فرابيه": "frappe",
    "شاي": "tea",
    "ماتشا": "matcha",
    "سحلب": "sahlab warm milk drink",
    "كركديه": "hibiscus tea",
    "ينسون": "herbal tea",
    "نعناع": "mint",
    "قهوة": "coffee",
    "نسكافيه": "coffee",
    "مثلج": "iced",
    "مثلجة": "iced",
    "بارد": "iced",
    "باردة": "iced",
    "ساخن": "hot",
    "ساخنة": "hot",
    "فانيليا": "vanilla",
    "كراميل": "caramel",
    "كاراميل": "caramel",
    "بندق": "hazelnut",
    "شوكولاتة": "chocolate",
    "شوكولاته": "chocolate",
    "شوكولا": "chocolate",
    "حليب": "milk",
    "لبن": "milk",
    "زبدة": "butter",
    "كرواسون": "butter croissant",
    "مافن": "muffin",
    "دونات": "donut",
    "تارت": "tart pastry",
    "كوكيز": "cookie",
    "براوني": "brownie",
    "كيك": "cake",
    "بانيني": "panini sandwich",
    "ساندوتش": "sandwich",
    "توست": "toast",
    "سلطة": "salad",
    "عصير": "juice",
    "برتقال": "orange",
    "مانجو": "mango",
    "فراولة": "strawberry",
    "ليمون": "lemon",
    "بلوبيري": "blueberry",
    "قرفة": "cinnamon",
    "شاي بحليب": "chai latte",
    "مخبوزات": "bakery pastry",
    "فرنسي": "french",
}

_AR_FILLER_PREFIXES = ("بال", "ال", "و")


def _translate_arabic_cafe_text(text: str) -> str:
    """Offline Arabic -> English café keyword normalization for stock image providers."""
    working = (text or "").strip()
    if not working or not _AR_CHAR_RE.search(working):
        return ""

    working = working.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي")
    working_orig = (text or "").strip()

    en_tokens: list[str] = []
    # Match multi-word Arabic café phrases first
    for ar_phrase, en_phrase in _AR_CAFE_PHRASES:
        norm_p = ar_phrase.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
        if ar_phrase in working_orig or norm_p in working:
            en_tokens.append(en_phrase)
            working = working.replace(norm_p, " ")

    # Match word tokens (handling attached Egyptian prepositions like بالكراميل -> كراميل)
    for raw_tok in re.split(r"[\s،,/·\-()]+", working):
        tok = raw_tok.strip()
        if not tok:
            continue
        if not _AR_CHAR_RE.search(tok):
            en_tokens.append(tok)
            continue
        matched = _AR_CAFE_WORDS.get(tok)
        if not matched:
            for pref in _AR_FILLER_PREFIXES:
                if tok.startswith(pref) and len(tok) > len(pref) + 1:
                    sub = tok[len(pref) :]
                    if sub in _AR_CAFE_WORDS:
                        matched = _AR_CAFE_WORDS[sub]
                        break
        if matched:
            en_tokens.append(matched)

    # Deduplicate adjacent/repeated English tokens while preserving order
    seen: set[str] = set()
    final_parts: list[str] = []
    for chunk in en_tokens:
        for w in chunk.split():
            lw = w.lower()
            if lw not in seen:
                seen.add(lw)
                final_parts.append(w)
    return " ".join(final_parts).strip()


def suggest_image_queries(
    product_name: str, category: str = "", product_name_en: str = ""
) -> list[str]:
    """Generate clean, lightweight search query variations for café products (EN + AR)."""
    explicit_en = _SIZE_NOISE_RE.sub("", product_name_en or "")
    explicit_en = re.sub(r"\s+", " ", explicit_en).strip(" -·,()")

    raw_name = _SIZE_NOISE_RE.sub("", product_name or "")
    raw_name = re.sub(r"\(\s*\)|\[\s*\]", "", raw_name)
    raw_name = re.sub(r"\s+", " ", raw_name).strip(" -·,()")
    if not raw_name and not explicit_en:
        return []

    bilingual_en = ""
    if raw_name and _AR_CHAR_RE.search(raw_name):
        sug = suggest_bilingual_name(raw_name, is_category=False)
        if sug.target_lang == "en" and sug.target_text:
            bilingual_en = sug.target_text

    translated_en = explicit_en or bilingual_en or _translate_arabic_cafe_text(raw_name)
    if translated_en and (_AR_CHAR_RE.search(raw_name) or explicit_en):
        # Stock photo search APIs index primarily in English; put crisp English terms first
        suggestions_ar: list[str] = []

        def _add_ar(q: str) -> None:
            cleaned = re.sub(r"\s+", " ", q).strip()
            if cleaned and cleaned.lower() not in {s.lower() for s in suggestions_ar}:
                suggestions_ar.append(cleaned)

        _add_ar(translated_en)
        cat_en = _translate_arabic_cafe_text(category) or (category if not _AR_CHAR_RE.search(category or "") else "")
        if "coffee" not in translated_en.lower() and any(
            w in translated_en.lower()
            for w in ("latte", "espresso", "cappuccino", "americano", "mocha", "macchiato", "flat white")
        ):
            _add_ar(f"{translated_en} coffee")
        elif cat_en and cat_en.lower() not in translated_en.lower() and cat_en.lower() != "all":
            _add_ar(f"{translated_en} {cat_en.lower()}")
        else:
            _add_ar(f"{translated_en} cafe")
        if raw_name:
            _add_ar(raw_name)
        return suggestions_ar[:3]

    lower = raw_name.lower()
    cat_lower = (category or "").lower()
    words = [w for w in re.split(r"\s+", raw_name) if w]

    suggestions: list[str] = [raw_name]

    def _add(q: str) -> None:
        cleaned = re.sub(r"\s+", " ", q).strip()
        if cleaned and cleaned.lower() not in {s.lower() for s in suggestions}:
            suggestions.append(cleaned)

    drink_terms = {
        "latte",
        "espresso",
        "cappuccino",
        "americano",
        "macchiato",
        "mocha",
        "matcha",
        "chai",
        "tea",
        "coffee",
        "cortado",
        "flat white",
    }
    pastry_terms = {"croissant", "muffin", "cake", "cookie", "tart", "scone", "donut", "brownie"}
    kitchen_terms = {"panini", "sandwich", "bagel", "toast", "salad", "wrap"}

    if any(t in lower for t in drink_terms) or "espresso" in cat_lower or "cold" in cat_lower or "tea" in cat_lower:
        if "tea" in lower or "matcha" in lower or "chai" in lower:
            _add(f"{lower} drink")
            _add(f"{words[-1].lower()} tea cup")
        else:
            if len(words) >= 2:
                _add(f"{' '.join(words[:-1]).lower()} coffee")
            else:
                _add(f"{lower} coffee cup")
            _add(f"{words[0].lower()} {words[-1].lower()} drink" if len(words) > 1 else f"{lower} drink")
    elif any(t in lower for t in pastry_terms) or "bak" in cat_lower:
        _add(f"{lower} pastry")
        _add(f"fresh {words[-1].lower()} bakery")
    elif any(t in lower for t in kitchen_terms) or "kitchen" in cat_lower:
        _add(f"{lower} cafe food")
        _add(f"fresh {words[-1].lower()} plate")
    elif "bean" in lower or "retail" in cat_lower:
        _add("roasted coffee beans")
        _add("coffee bag cafe")
    else:
        if category and category.lower() not in lower:
            _add(f"{lower} {category.lower()}")
        _add(f"{lower} cafe")

    return suggestions[:3]


def _is_disallowed_ip(ip_obj: ipaddress._BaseAddress) -> bool:
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def validate_remote_image_url(url: str, *, resolve_dns: bool = False) -> str:
    """Validate that remote URL uses HTTPS and targets a public host (no SSRF)."""
    cleaned = (url or "").strip()
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme.lower() != "https":
        raise ImageSearchError(tr("backend.err.img_https_only"), code="invalid")
    if not parsed.netloc or not parsed.hostname:
        raise ImageSearchError(tr("backend.err.img_no_host"), code="invalid")

    host = parsed.hostname.strip().lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ImageSearchError(tr("backend.err.img_local_host"), code="invalid")

    try:
        ip_lit = ipaddress.ip_address(host)
        if _is_disallowed_ip(ip_lit):
            raise ImageSearchError(tr("backend.err.img_private_ip"), code="invalid")
    except ValueError:
        pass

    if resolve_dns:
        try:
            infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
            for info in infos:
                addr_str = info[4][0]
                ip_resolved = ipaddress.ip_address(addr_str)
                if _is_disallowed_ip(ip_resolved):
                    raise ImageSearchError(tr("backend.err.img_dns_restricted"), code="invalid")
        except ImageSearchError:
            raise
        except OSError as exc:
            raise ImageSearchError(tr("backend.err.search_network"), code="offline") from exc

    return cleaned


def _http_get_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
    max_bytes: int = MAX_PRODUCT_IMAGE_BYTES,
    cancel_token: CancellationToken | None = None,
    resolve_dns: bool = True,
) -> tuple[bytes, str]:
    validate_remote_image_url(url, resolve_dns=resolve_dns)
    if cancel_token and cancel_token.is_cancelled:
        raise ImageSearchError(tr("backend.err.img_cancelled"), code="cancelled")

    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            validate_remote_image_url(final_url, resolve_dns=False)
            content_type = (resp.headers.get("Content-Type") or "").lower()
            content_length = resp.headers.get("Content-Length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                raise ImageSearchError(
                    tr("backend.err.img_remote_limit", mb=max_bytes // (1024 * 1024)),
                    code="invalid",
                )

            chunks: list[bytes] = []
            total = 0
            while True:
                if cancel_token and cancel_token.is_cancelled:
                    raise ImageSearchError(tr("backend.err.img_cancelled"), code="cancelled")
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ImageSearchError(
                        tr("backend.err.img_remote_limit", mb=max_bytes // (1024 * 1024)),
                        code="invalid",
                    )
                chunks.append(chunk)
            return b"".join(chunks), content_type
    except ImageSearchError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ImageSearchError(tr("backend.err.search_auth"), code="auth") from exc
        if exc.code == 429:
            raise ImageSearchError(tr("backend.err.search_rate_limit"), code="rate_limit") from exc
        if exc.code in {502, 503, 504}:
            raise ImageSearchError(
                tr("backend.err.search_http", code=exc.code),
                code="timeout",
            ) from exc
        raise ImageSearchError(tr("backend.err.search_http", code=exc.code), code="error") from exc
    except TimeoutError as exc:
        raise ImageSearchError(tr("backend.err.search_timeout"), code="timeout") from exc
    except urllib.error.URLError as exc:
        reason_str = str(exc.reason).lower()
        if "timed out" in reason_str:
            raise ImageSearchError(tr("backend.err.search_timeout"), code="timeout") from exc
        raise ImageSearchError(tr("backend.err.search_network"), code="offline") from exc


class OpenverseProvider:
    name = "openverse"
    ENDPOINT = "https://api.openverse.org/v1/images/"

    def search(
        self, query: str, limit: int = 8, cancel_token: CancellationToken | None = None
    ) -> list[ImageSearchResult]:
        clean_q = (query or "").strip()
        if not clean_q:
            return []

        params = urllib.parse.urlencode(
            {
                "q": clean_q,
                "license": "cc0,pdm",
                "page_size": max(1, min(20, int(limit))),
                "mature": "false",
                "format": "json",
            }
        )
        url = f"{self.ENDPOINT}?{params}"
        raw, _ = _http_get_bytes(
            url,
            headers={"Accept": "application/json"},
            timeout=SEARCH_TIMEOUT_SECONDS,
            max_bytes=2 * 1024 * 1024,
            cancel_token=cancel_token,
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImageSearchError("Received invalid JSON from Openverse API.", code="invalid") from exc

        results: list[ImageSearchResult] = []
        for item in payload.get("results") or []:
            license_code = (item.get("license") or "").strip().lower()
            if license_code not in ALLOWED_OPENVERSE_LICENSES:
                continue

            img_url = (item.get("url") or "").strip()
            thumb_url = (item.get("thumbnail") or img_url).strip()
            try:
                validate_remote_image_url(img_url)
                validate_remote_image_url(thumb_url)
            except ImageSearchError:
                continue

            creator = (item.get("creator") or "").strip() or "Openverse contributor"
            lic_upper = license_code.upper()
            attribution = (item.get("attribution") or "").strip()
            if not attribution:
                attribution = f"Photo by {creator} ({lic_upper}) via Openverse"

            results.append(
                ImageSearchResult(
                    id=str(item.get("id") or img_url),
                    title=(item.get("title") or clean_q).strip(),
                    thumbnail_url=thumb_url,
                    image_url=img_url,
                    source_provider="openverse",
                    creator=creator,
                    creator_url=(item.get("creator_url") or "").strip(),
                    license_code=lic_upper,
                    license_url=(item.get("license_url") or "").strip(),
                    attribution=attribution,
                    landing_url=(item.get("foreign_landing_url") or "").strip(),
                    width=int(item.get("width") or 0),
                    height=int(item.get("height") or 0),
                )
            )
            if len(results) >= limit:
                break
        return results


class PexelsProvider:
    name = "pexels"
    ENDPOINT = "https://api.pexels.com/v1/search"

    def __init__(self, api_key: str) -> None:
        self.api_key = (api_key or "").strip()

    def search(
        self, query: str, limit: int = 8, cancel_token: CancellationToken | None = None
    ) -> list[ImageSearchResult]:
        if not self.api_key:
            raise ImageSearchError(tr("backend.err.search_pexels_key"), code="auth")
        clean_q = (query or "").strip()
        if not clean_q:
            return []

        params = urllib.parse.urlencode(
            {
                "query": clean_q,
                "per_page": max(1, min(15, int(limit))),
            }
        )
        url = f"{self.ENDPOINT}?{params}"
        raw, _ = _http_get_bytes(
            url,
            headers={"Authorization": self.api_key, "Accept": "application/json"},
            timeout=SEARCH_TIMEOUT_SECONDS,
            max_bytes=2 * 1024 * 1024,
            cancel_token=cancel_token,
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImageSearchError("Received invalid JSON from Pexels API.", code="invalid") from exc

        results: list[ImageSearchResult] = []
        for photo in payload.get("photos") or []:
            src = photo.get("src") or {}
            thumb_url = (src.get("small") or src.get("medium") or src.get("tiny") or "").strip()
            img_url = (src.get("large") or src.get("medium") or src.get("original") or "").strip()
            try:
                validate_remote_image_url(thumb_url)
                validate_remote_image_url(img_url)
            except ImageSearchError:
                continue

            photographer = (photo.get("photographer") or "Pexels contributor").strip()
            landing = (photo.get("url") or "https://www.pexels.com").strip()
            results.append(
                ImageSearchResult(
                    id=str(photo.get("id") or img_url),
                    title=(photo.get("alt") or clean_q).strip(),
                    thumbnail_url=thumb_url,
                    image_url=img_url,
                    source_provider="pexels",
                    creator=photographer,
                    creator_url=(photo.get("photographer_url") or "").strip(),
                    license_code="Pexels License",
                    license_url="https://www.pexels.com/license/",
                    attribution=f"Photo by {photographer} on Pexels",
                    landing_url=landing,
                    width=int(photo.get("width") or 0),
                    height=int(photo.get("height") or 0),
                )
            )
            if len(results) >= limit:
                break
        return results


def build_image_provider(provider_name: str, pexels_api_key: str = "") -> ImageSearchProvider:
    clean = validate_image_provider(provider_name)
    if clean == "pexels":
        return PexelsProvider(pexels_api_key)
    return OpenverseProvider()


def build_google_images_search_url(query: str) -> str:
    clean = (query or "").strip() or "coffee"
    return f"https://www.google.com/search?tbm=isch&q={urllib.parse.quote_plus(clean)}"


def decode_and_validate_image_bytes(raw_bytes: bytes, content_type: str = "") -> Image.Image:
    if not raw_bytes:
        raise ImageSearchError(tr("backend.err.img_empty"), code="invalid")
    if len(raw_bytes) > MAX_PRODUCT_IMAGE_BYTES:
        raise ImageSearchError(
            tr("backend.err.img_size", mb=f"{MAX_PRODUCT_IMAGE_BYTES // (1024 * 1024)}.0"),
            code="invalid",
        )
    ct = (content_type or "").lower()
    if ct and ("html" in ct or "svg" in ct or "xml" in ct or "text/" in ct):
        raise ImageSearchError(tr("backend.err.download_bad_image"), code="invalid")

    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.load()
            fmt = (img.format or "").upper()
            if fmt not in {"JPEG", "PNG", "WEBP", "BMP", "GIF"}:
                raise ImageSearchError(
                    tr("backend.err.img_format", ext=fmt or "unknown"),
                    code="invalid",
                )
            if img.width < 24 or img.height < 24:
                raise ImageSearchError(tr("backend.err.img_resolution_small"), code="invalid")
            transposed = ImageOps.exif_transpose(img)
            return transposed.copy()
    except ImageSearchError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ImageSearchError(tr("backend.err.img_invalid", exc=exc), code="invalid") from exc


def has_alpha_transparency(raw_bytes: bytes) -> bool:
    """Return True when image carries non-opaque alpha pixels (transparent PNG/WebP)."""
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.load()
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                alpha = img.convert("RGBA").getchannel("A")
                min_a, _ = alpha.getextrema()
                return min_a < 250
    except Exception:
        return False
    return False


def remove_white_background_cutout(raw_bytes: bytes, *, tolerance: int = 24) -> bytes:
    """Safely flood-fill exterior white/near-white studio pixels to transparent PNG."""
    img = decode_and_validate_image_bytes(raw_bytes).convert("RGBA")
    w, h = img.size
    pixels = img.load()

    def is_bg_candidate(x: int, y: int) -> bool:
        r, g, b, a = pixels[x, y]
        if a < 10:
            return True
        hi = max(r, g, b)
        lo = min(r, g, b)
        return lo >= (255 - tolerance) and (hi - lo) <= 22

    visited = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()

    for x in range(w):
        for y in (0, h - 1):
            idx = y * w + x
            if not visited[idx] and is_bg_candidate(x, y):
                visited[idx] = 1
                q.append((x, y))
    for y in range(1, h - 1):
        for x in (0, w - 1):
            idx = y * w + x
            if not visited[idx] and is_bg_candidate(x, y):
                visited[idx] = 1
                q.append((x, y))

    while q:
        cx, cy = q.popleft()
        pixels[cx, cy] = (255, 255, 255, 0)
        for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
            if 0 <= nx < w and 0 <= ny < h:
                nidx = ny * w + nx
                if not visited[nidx] and is_bg_candidate(nx, ny):
                    visited[nidx] = 1
                    q.append((nx, ny))

    # Trim outer empty margin softly (keeping small 6% padding) and return PNG
    bbox = img.getbbox()
    if bbox:
        bw = bbox[2] - bbox[0]
        bh = bbox[3] - bbox[1]
        pad = max(4, int(max(bw, bh) * 0.05))
        crop_box = (
            max(0, bbox[0] - pad),
            max(0, bbox[1] - pad),
            min(w, bbox[2] + pad),
            min(h, bbox[3] + pad),
        )
        img = img.crop(crop_box)

    out_buf = io.BytesIO()
    img.save(out_buf, format="PNG", optimize=True)
    return out_buf.getvalue()


def render_cropped_product_image(
    raw_bytes: bytes,
    *,
    fit_mode: str = "cover",
    zoom: float = 1.0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    output_size: int = OUTPUT_IMAGE_SIZE,
    preserve_alpha: bool = False,
) -> bytes:
    """Apply fit/crop/zoom adjustments and emit optimized JPEG (or transparent PNG)."""
    img = decode_and_validate_image_bytes(raw_bytes)
    zoom_clamped = max(1.0, min(2.2, float(zoom)))
    off_x = max(-1.0, min(1.0, float(offset_x)))
    off_y = max(-1.0, min(1.0, float(offset_y)))

    has_alpha = preserve_alpha and (
        img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    )
    if has_alpha:
        work_img = img.convert("RGBA")
    elif img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[3])
        work_img = bg.convert("RGB")
    else:
        work_img = img.convert("RGB")

    w, h = work_img.size
    if fit_mode == "contain":
        scale = min(output_size / w, output_size / h)
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        resized = work_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        canvas = (
            Image.new("RGBA", (output_size, output_size), (0, 0, 0, 0))
            if has_alpha
            else Image.new("RGB", (output_size, output_size), (255, 255, 255))
        )
        paste_x = (output_size - new_w) // 2
        paste_y = (output_size - new_h) // 2
        if has_alpha:
            canvas.paste(resized, (paste_x, paste_y), mask=resized.split()[3])
        else:
            canvas.paste(resized, (paste_x, paste_y))
        final_img = canvas
    else:
        base_side = min(w, h)
        crop_side = max(16, int(round(base_side / zoom_clamped)))
        max_dx = max(0, (w - crop_side) // 2)
        max_dy = max(0, (h - crop_side) // 2)
        cx = w // 2 + int(round(off_x * max_dx))
        cy = h // 2 + int(round(off_y * max_dy))
        left = max(0, min(w - crop_side, cx - crop_side // 2))
        top = max(0, min(h - crop_side, cy - crop_side // 2))
        cropped = work_img.crop((left, top, left + crop_side, top + crop_side))
        final_img = cropped.resize((output_size, output_size), Image.Resampling.LANCZOS)

    out_buf = io.BytesIO()
    if has_alpha:
        final_img.save(out_buf, format="PNG", optimize=True)
    else:
        final_img.save(out_buf, format="JPEG", quality=88, optimize=True)
    return out_buf.getvalue()


def save_optimized_product_image(optimized_bytes: bytes) -> str:
    """Persist image under AppData/product_images/ using deterministic content hash."""
    digest = hashlib.sha256(optimized_bytes).hexdigest()[:16]
    ext = ".png" if optimized_bytes.startswith(b"\x89PNG\r\n\x1a\n") else ".jpg"
    filename = f"prod_{digest}{ext}"
    target_path = get_product_images_dir() / filename
    if not target_path.exists():
        target_path.write_bytes(optimized_bytes)
    return filename


def download_remote_image_bytes(
    url: str,
    *,
    max_bytes: int = MAX_PRODUCT_IMAGE_BYTES,
    cancel_token: CancellationToken | None = None,
) -> bytes:
    raw, content_type = _http_get_bytes(
        url,
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
        max_bytes=max_bytes,
        cancel_token=cancel_token,
    )
    decode_and_validate_image_bytes(raw, content_type=content_type)
    return raw


def read_local_product_image_bytes(file_path: Path) -> bytes:
    if not file_path.exists() or not file_path.is_file():
        raise ImageSearchError(tr("backend.err.img_missing"), code="invalid")
    ext = file_path.suffix.lower()
    if ext not in SUPPORTED_PRODUCT_IMAGE_EXTS:
        raise ImageSearchError(tr("backend.err.img_format", ext=ext), code="invalid")
    size = file_path.stat().st_size
    if size <= 0:
        raise ImageSearchError(tr("backend.err.img_empty"), code="invalid")
    if size > MAX_PRODUCT_IMAGE_BYTES:
        mb = size / (1024 * 1024)
        raise ImageSearchError(tr("backend.err.img_size", mb=f"{mb:.1f}"), code="invalid")
    raw = file_path.read_bytes()
    decode_and_validate_image_bytes(raw)
    return raw


def resolve_product_image_path(image_filename: str) -> Path | None:
    clean = (image_filename or "").strip()
    if not clean or "/" in clean or "\\" in clean or ".." in clean:
        return None
    p = get_product_images_dir() / clean
    if p.exists() and p.is_file() and p.stat().st_size > 0:
        return p
    return None


def get_image_cache_stats(referenced_filenames: set[str]) -> tuple[int, int, int, int]:
    """Return (total_files, total_bytes, unused_files, unused_bytes) in product_images dir."""
    img_dir = get_product_images_dir()
    total_files = 0
    total_bytes = 0
    unused_files = 0
    unused_bytes = 0
    for entry in img_dir.iterdir():
        if entry.is_file():
            sz = entry.stat().st_size
            total_files += 1
            total_bytes += sz
            if entry.name not in referenced_filenames:
                unused_files += 1
                unused_bytes += sz
    return total_files, total_bytes, unused_files, unused_bytes


def clear_unused_cached_images(referenced_filenames: set[str]) -> tuple[int, int]:
    """Remove unreferenced files from product_images dir. Returns (deleted_count, freed_bytes)."""
    img_dir = get_product_images_dir()
    deleted = 0
    freed = 0
    for entry in img_dir.iterdir():
        if entry.is_file() and entry.name not in referenced_filenames:
            sz = entry.stat().st_size
            try:
                entry.unlink()
                deleted += 1
                freed += sz
            except OSError:
                continue
    return deleted, freed


class ImageSearchWorker(QThread):
    """Non-blocking background worker for image provider search queries."""

    results_ready = Signal(str, list)  # query, list[ImageSearchResult]
    search_failed = Signal(str, str)   # query, friendly error message

    def __init__(
        self,
        provider_name: str,
        pexels_api_key: str,
        query: str,
        limit: int = 8,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.provider_name = provider_name
        self.pexels_api_key = pexels_api_key
        self.query = query
        self.limit = limit
        self.cancel_token = CancellationToken()

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def run(self) -> None:
        try:
            provider = build_image_provider(self.provider_name, self.pexels_api_key)
            results = provider.search(self.query, limit=self.limit, cancel_token=self.cancel_token)
            if not self.cancel_token.is_cancelled:
                self.results_ready.emit(self.query, results)
        except ImageSearchError as exc:
            if exc.code != "cancelled" and not self.cancel_token.is_cancelled:
                self.search_failed.emit(self.query, str(exc))
        except Exception as exc:
            if not self.cancel_token.is_cancelled:
                self.search_failed.emit(self.query, f"Unexpected search error: {exc}")


class ThumbnailBatchWorker(QThread):
    """Downloads small thumbnails concurrently without freezing GUI."""

    thumbnail_loaded = Signal(str, bytes)  # url, image_bytes

    def __init__(self, urls: list[str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.urls = list(dict.fromkeys(u for u in urls if u))
        self.cancel_token = CancellationToken()

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def run(self) -> None:
        for url in self.urls:
            if self.cancel_token.is_cancelled:
                return
            try:
                raw, ct = _http_get_bytes(
                    url,
                    timeout=10.0,
                    max_bytes=MAX_THUMBNAIL_BYTES,
                    cancel_token=self.cancel_token,
                    resolve_dns=True,
                )
                decode_and_validate_image_bytes(raw, content_type=ct)
                if not self.cancel_token.is_cancelled:
                    self.thumbnail_loaded.emit(url, raw)
            except ImageSearchError:
                continue
            except Exception:
                continue


class ImageDownloadWorker(QThread):
    """Downloads a chosen high-res image asynchronously before crop/confirmation."""

    image_downloaded = Signal(object, bytes)  # ImageSearchResult, raw_bytes
    download_failed = Signal(str)             # error message

    def __init__(self, item: ImageSearchResult, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self.cancel_token = CancellationToken()

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def run(self) -> None:
        try:
            raw = download_remote_image_bytes(self.item.image_url, cancel_token=self.cancel_token)
            if not self.cancel_token.is_cancelled:
                self.image_downloaded.emit(self.item, raw)
        except ImageSearchError as exc:
            if exc.code != "cancelled" and not self.cancel_token.is_cancelled:
                self.download_failed.emit(str(exc))
        except Exception as exc:
            if not self.cancel_token.is_cancelled:
                self.download_failed.emit(f"Could not download selected photo: {exc}")
