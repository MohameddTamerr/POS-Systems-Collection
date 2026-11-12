import io
import json
import urllib.error
from pathlib import Path

import pytest
from PIL import Image

from cafe_pos import image_search as img_mod
from cafe_pos.paths import get_product_images_dir


def _make_sample_png_bytes(width: int = 160, height: int = 100, rgba: bool = True) -> bytes:
    mode = "RGBA" if rgba else "RGB"
    color = (114, 145, 123, 220) if rgba else (114, 145, 123)
    img = Image.new(mode, (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_suggest_image_queries_generates_clean_keywords():
    latte_qs = img_mod.suggest_image_queries("Iced Vanilla Latte (Large)", "Cold Drinks")
    assert len(latte_qs) == 3
    assert latte_qs[0] == "Iced Vanilla Latte"
    assert any("coffee" in q.lower() or "drink" in q.lower() for q in latte_qs[1:])

    croissant_qs = img_mod.suggest_image_queries("Butter Croissant", "Bakery")
    assert croissant_qs[0] == "Butter Croissant"
    assert any("pastry" in q.lower() or "bakery" in q.lower() for q in croissant_qs[1:])

    assert img_mod.suggest_image_queries("") == []


def test_validate_remote_image_url_blocks_ssrf_and_non_https():
    assert img_mod.validate_remote_image_url("https://images.pexels.com/photos/123.jpeg").startswith(
        "https://"
    )

    for unsafe in (
        "http://example.com/photo.jpg",
        "file:///C:/Windows/win.ini",
        "https://localhost/admin",
        "https://127.0.0.1/internal.png",
        "https://10.0.0.5/secret.jpg",
        "https://169.254.169.254/latest/meta-data",
        "https://printer.local/img.png",
    ):
        with pytest.raises(img_mod.ImageSearchError):
            img_mod.validate_remote_image_url(unsafe)


def test_openverse_provider_filters_open_licenses_and_unsafe_urls(monkeypatch):
    fake_payload = {
        "results": [
            {
                "id": "ov-1",
                "title": "Hot Cappuccino",
                "url": "https://cdn.example.org/capp.jpg",
                "thumbnail": "https://cdn.example.org/capp_thumb.jpg",
                "license": "cc0",
                "creator": "Alice",
                "foreign_landing_url": "https://openverse.org/image/ov-1",
                "width": 800,
                "height": 600,
            },
            {
                "id": "ov-nonopen",
                "title": "Restricted Photo",
                "url": "https://cdn.example.org/restricted.jpg",
                "thumbnail": "https://cdn.example.org/restricted_t.jpg",
                "license": "by-nc",
                "creator": "Bob",
            },
            {
                "id": "ov-http",
                "title": "Insecure Photo",
                "url": "http://cdn.example.org/insecure.jpg",
                "license": "pdm",
            },
        ]
    }

    monkeypatch.setattr(
        img_mod,
        "_http_get_bytes",
        lambda url, **_kw: (json.dumps(fake_payload).encode("utf-8"), "application/json"),
    )

    provider = img_mod.OpenverseProvider()
    results = provider.search("cappuccino", limit=8)
    assert len(results) == 1
    assert results[0].id == "ov-1"
    assert results[0].badge_label == "CC0"
    assert "Alice" in results[0].attribution


def test_pexels_provider_requires_key_and_parses_results(monkeypatch):
    empty_key = img_mod.PexelsProvider("")
    with pytest.raises(img_mod.ImageSearchError) as exc_info:
        empty_key.search("espresso")
    assert exc_info.value.code == "auth"

    fake_payload = {
        "photos": [
            {
                "id": 9981,
                "alt": "Latte Art",
                "url": "https://www.pexels.com/photo/9981/",
                "photographer": "Roaster Cam",
                "width": 1200,
                "height": 1200,
                "src": {
                    "small": "https://images.pexels.com/photos/9981/small.jpg",
                    "large": "https://images.pexels.com/photos/9981/large.jpg",
                },
            }
        ]
    }
    monkeypatch.setattr(
        img_mod,
        "_http_get_bytes",
        lambda url, **_kw: (json.dumps(fake_payload).encode("utf-8"), "application/json"),
    )

    provider = img_mod.PexelsProvider("demo-key-123")
    results = provider.search("latte")
    assert len(results) == 1
    assert results[0].source_provider == "pexels"
    assert results[0].badge_label == "Pexels"
    assert "Roaster Cam" in results[0].attribution


def test_build_google_images_search_url():
    url = img_mod.build_google_images_search_url("Caramel Macchiato")
    assert url.startswith("https://www.google.com/search?tbm=isch&q=")
    assert "Caramel+Macchiato" in url


def test_render_cropped_image_save_and_cache_cleanup(isolated_data_dir: Path):
    raw_png = _make_sample_png_bytes(width=200, height=120, rgba=True)

    # Cover crop produces exact square JPEG
    cover_jpg = img_mod.render_cropped_product_image(raw_png, fit_mode="cover", zoom=1.4)
    with Image.open(io.BytesIO(cover_jpg)) as out_cov:
        assert out_cov.format == "JPEG"
        assert out_cov.size == (img_mod.OUTPUT_IMAGE_SIZE, img_mod.OUTPUT_IMAGE_SIZE)

    # Contain crop flattens alpha over white and fits inside square JPEG
    contain_jpg = img_mod.render_cropped_product_image(raw_png, fit_mode="contain")
    with Image.open(io.BytesIO(contain_jpg)) as out_con:
        assert out_con.format == "JPEG"
        assert out_con.size == (img_mod.OUTPUT_IMAGE_SIZE, img_mod.OUTPUT_IMAGE_SIZE)

    fn1 = img_mod.save_optimized_product_image(cover_jpg)
    fn2 = img_mod.save_optimized_product_image(contain_jpg)
    assert fn1 != fn2
    assert img_mod.resolve_product_image_path(fn1) == get_product_images_dir() / fn1
    # Content hashing deduplicates identical image saves
    assert img_mod.save_optimized_product_image(cover_jpg) == fn1

    # Path traversal rejected
    assert img_mod.resolve_product_image_path("../secrets.db") is None

    # Cache stats & cleanup
    total_n, total_b, unused_n, unused_b = img_mod.get_image_cache_stats({fn1})
    assert total_n == 2
    assert unused_n == 1
    assert unused_b > 0

    deleted, freed = img_mod.clear_unused_cached_images({fn1})
    assert deleted == 1
    assert freed == unused_b
    assert img_mod.resolve_product_image_path(fn1) is not None
    assert img_mod.resolve_product_image_path(fn2) is None


def test_transparent_cutout_png_preserved_and_white_bg_removal(isolated_data_dir: Path):
    # White studio background surrounding an opaque coffee cup rectangle
    studio = Image.new("RGB", (120, 120), (255, 255, 255))
    for x in range(36, 84):
        for y in range(28, 92):
            studio.putpixel((x, y), (92, 58, 40))
    buf = io.BytesIO()
    studio.save(buf, format="PNG")
    studio_bytes = buf.getvalue()

    assert img_mod.has_alpha_transparency(studio_bytes) is False

    cutout_bytes = img_mod.remove_white_background_cutout(studio_bytes)
    assert img_mod.has_alpha_transparency(cutout_bytes) is True

    with Image.open(io.BytesIO(cutout_bytes)) as cut_img:
        assert cut_img.format == "PNG"
        assert cut_img.getpixel((0, 0))[3] == 0
        cx, cy = cut_img.width // 2, cut_img.height // 2
        assert cut_img.getpixel((cx, cy))[3] == 255

    preserved_png = img_mod.render_cropped_product_image(
        cutout_bytes, fit_mode="contain", preserve_alpha=True
    )
    with Image.open(io.BytesIO(preserved_png)) as out_png:
        assert out_png.format == "PNG"
        assert out_png.mode == "RGBA"
        assert out_png.getpixel((0, 0))[3] == 0

    fn_png = img_mod.save_optimized_product_image(preserved_png)
    assert fn_png.endswith(".png")
    assert (get_product_images_dir() / fn_png).exists()

