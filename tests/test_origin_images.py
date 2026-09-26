"""
Offline tests for the overseas filter and the thumbnail layer.

No network: image downloads are driven through a fake session.

    pytest -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import daraz_images as di  # noqa: E402
import daraz_np_server as srv  # noqa: E402

D = srv.DarazNepal


# --------------------------------------------------------------------------- #
# Origin classification
# --------------------------------------------------------------------------- #


class TestClassifyOrigin:
    def test_nepali_provinces_are_domestic(self):
        for place in ("Bagmati Province", "Koshi", "Lumbini Province",
                      "Sudurpashchim", "Gandaki Province"):
            assert D.classify_origin(place) == "nepal", place

    def test_nepali_cities_are_domestic(self):
        for place in ("Kathmandu", "Pokhara", "Biratnagar", "Lalitpur"):
            assert D.classify_origin(place) == "nepal", place

    def test_case_and_whitespace_insensitive(self):
        assert D.classify_origin("  bagmati province  ") == "nepal"
        assert D.classify_origin("KATHMANDU") == "nepal"

    def test_compound_location_still_domestic(self):
        assert D.classify_origin("Bagmati Province, Kathmandu") == "nepal"

    def test_explicit_overseas_markers(self):
        for place in ("Overseas", "China", "Hong Kong", "Shenzhen", "Singapore"):
            assert D.classify_origin(place) == "overseas", place

    def test_marker_in_a_badge(self):
        assert D.classify_origin("Bagmati Province", ["Overseas"]) == "overseas"
        assert D.classify_origin("Kathmandu", ["Global Collection"]) == "overseas"

    def test_marker_in_seller_name(self):
        assert D.classify_origin("Kathmandu", [], "Global Imports Co") == "overseas"

    def test_unrecognised_place_is_overseas(self):
        assert D.classify_origin("Berlin") == "overseas"

    def test_missing_location_is_unknown(self):
        assert D.classify_origin(None) == "unknown"
        assert D.classify_origin("") == "unknown"
        assert D.classify_origin("   ") == "unknown"


class TestOriginOnNormalisedProduct:
    base = {
        "name": "Wireless Mouse",
        "productUrl": "//www.daraz.com.np/products/mouse-i1.html",
        "price": "999",
    }

    def test_domestic_item(self):
        product = srv.daraz._normalise({**self.base, "location": "Bagmati Province"})
        assert product.origin == "nepal"

    def test_overseas_item(self):
        product = srv.daraz._normalise({**self.base, "location": "Overseas"})
        assert product.origin == "overseas"

    def test_unknown_when_daraz_omits_location(self):
        product = srv.daraz._normalise(self.base)
        assert product.origin == "unknown"


class TestOverseasTagInOutput:
    def _product(self, origin, location):
        return srv.Product(
            name="Test Item", url="https://www.daraz.com.np/products/x.html",
            price=500.0, location=location, origin=origin,
        )

    def test_overseas_is_tagged(self):
        text = srv._format_products([self._product("overseas", "China")], "Results")
        assert "Overseas" in text

    def test_domestic_is_not_tagged(self):
        text = srv._format_products([self._product("nepal", "Bagmati Province")], "Results")
        assert "Overseas" not in text

    def test_unknown_is_not_tagged(self):
        text = srv._format_products([self._product("unknown", None)], "Results")
        assert "Overseas" not in text

    def test_tag_not_duplicated_when_location_already_says_overseas(self):
        text = srv._format_products([self._product("overseas", "Overseas")], "Results")
        assert text.count("Overseas") == 1

    def test_tag_still_shown_for_a_named_foreign_place(self):
        text = srv._format_products([self._product("overseas", "China")], "Results")
        assert "China" in text and "Overseas" in text


class TestShipsFromFilter:
    """The filter runs client-side, so it holds even when Daraz ignores the param."""

    raw = [
        {"name": "Local Mouse", "productUrl": "/products/a-i1.html", "price": "500",
         "location": "Bagmati Province"},
        {"name": "Import Mouse", "productUrl": "/products/b-i2.html", "price": "300",
         "location": "Overseas"},
        {"name": "Mystery Mouse", "productUrl": "/products/c-i3.html", "price": "400"},
    ]

    def _search(self, monkeypatch, ships_from):
        pages = {"n": 0}

        def fake_fetch(query, page=1, **kwargs):
            pages["n"] += 1
            if pages["n"] > 1:
                return []
            return [srv.daraz._normalise(i) for i in self.raw]

        monkeypatch.setattr(srv.daraz, "fetch_page", fake_fetch)
        return srv.daraz.search("mouse", limit=10, pages=1, ships_from=ships_from)

    def test_any_returns_everything(self, monkeypatch):
        assert len(self._search(monkeypatch, "any")) == 3

    def test_nepal_drops_overseas_but_keeps_unknown(self, monkeypatch):
        names = [p.name for p in self._search(monkeypatch, "nepal")]
        assert "Import Mouse" not in names
        assert "Local Mouse" in names
        # We don't know where the mystery item ships from, so we don't hide it.
        assert "Mystery Mouse" in names

    def test_overseas_keeps_only_imports(self, monkeypatch):
        names = [p.name for p in self._search(monkeypatch, "overseas")]
        assert names == ["Import Mouse"]


class TestShipsFromValidation:
    def test_unknown_value_falls_back_to_any(self, monkeypatch):
        captured = {}

        def fake_search(query, **kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(srv.daraz, "search", fake_search)
        monkeypatch.setattr(srv.daraz, "browser_search", lambda q: [])
        srv._search_daraz("mouse", ships_from="mars")
        assert captured["ships_from"] == "any"

    def test_value_is_normalised(self, monkeypatch):
        captured = {}

        def fake_search(query, **kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(srv.daraz, "search", fake_search)
        monkeypatch.setattr(srv.daraz, "browser_search", lambda q: [])
        srv._search_daraz("mouse", ships_from="  NEPAL  ")
        assert captured["ships_from"] == "nepal"


class TestNoServerSideLocationParam:
    """
    Regression: sending `location=Nepal` made Daraz return an empty payload.
    Search then fell back to the browser scraper and produced a page of results
    with no prices — a silent, plausible-looking failure. The filter is
    client-side only now.
    """

    def _params(self, monkeypatch, ships_from):
        seen = {}

        class FakeResponse:
            status_code = 200
            url = "x"

            @staticmethod
            def json():
                return {"mods": {"listItems": []}}

        def fake_get(url, params=None, **kwargs):
            seen.update(params or {})
            return FakeResponse()

        monkeypatch.setattr(srv.daraz, "_warm_up", lambda: None)
        monkeypatch.setattr(srv.daraz.session, "get", fake_get)
        srv.daraz.fetch_page("mouse", 1, ships_from=ships_from)
        return seen

    def test_nepal_sends_no_location_param(self, monkeypatch):
        assert "location" not in self._params(monkeypatch, "nepal")

    def test_overseas_sends_no_location_param(self, monkeypatch):
        assert "location" not in self._params(monkeypatch, "overseas")

    def test_any_sends_no_location_param(self, monkeypatch):
        assert "location" not in self._params(monkeypatch, "any")


# --------------------------------------------------------------------------- #
# Thumbnail URL rewriting
# --------------------------------------------------------------------------- #


class TestThumbnailUrl:
    def test_daraz_jpg_gets_a_size_suffix(self):
        out = di.thumbnail_url("https://static-01.daraz.com.np/p/abc.jpg", 300, 80)
        assert out == "https://static-01.daraz.com.np/p/abc.jpg_300x300q80.jpg_.webp"

    def test_png_is_normalised_to_jpg_before_sizing(self):
        out = di.thumbnail_url("https://static-01.daraz.com.np/p/abc.png", 200, 70)
        assert out == "https://static-01.daraz.com.np/p/abc.jpg_200x200q70.jpg_.webp"

    def test_already_sized_url_is_left_alone(self):
        url = "https://static-01.daraz.com.np/p/abc.jpg_300x300q80.jpg_.webp"
        assert di.thumbnail_url(url) == url

    def test_non_daraz_host_untouched(self):
        assert di.thumbnail_url("https://example.com/x.jpg") == "https://example.com/x.jpg"

    def test_empty_and_odd_inputs(self):
        assert di.thumbnail_url("") == ""
        assert di.thumbnail_url(None) == ""
        # No recognisable extension: nothing safe to rewrite.
        odd = "https://static-01.daraz.com.np/p/abc"
        assert di.thumbnail_url(odd) == odd


# --------------------------------------------------------------------------- #
# Thumbnail downloading
# --------------------------------------------------------------------------- #


class FakeResponse:
    def __init__(self, status=200, content_type="image/jpeg", body=b"x" * 100):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self._body = body
        self.closed = False

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        result = self._responses.get(url)
        if result is None:
            raise AssertionError(f"unexpected fetch: {url}")
        if isinstance(result, Exception):
            raise result
        return result


class TestFetchThumbnail:
    original = "https://static-01.daraz.com.np/p/abc.jpg"
    sized = "https://static-01.daraz.com.np/p/abc.jpg_300x300q80.jpg_.webp"

    def test_prefers_the_small_version(self):
        session = FakeSession({self.sized: FakeResponse(content_type="image/webp")})
        data, fmt = di.fetch_thumbnail(session, self.original)
        assert fmt == "webp"
        assert session.calls == [self.sized]

    def test_falls_back_to_the_original(self):
        session = FakeSession({
            self.sized: FakeResponse(status=404),
            self.original: FakeResponse(content_type="image/jpeg"),
        })
        data, fmt = di.fetch_thumbnail(session, self.original)
        assert fmt == "jpeg"
        assert session.calls == [self.sized, self.original]

    def test_unsupported_type_is_dropped(self):
        session = FakeSession({
            self.sized: FakeResponse(content_type="text/html"),
            self.original: FakeResponse(content_type="text/html"),
        })
        assert di.fetch_thumbnail(session, self.original) is None

    def test_oversized_image_is_dropped_not_truncated(self, monkeypatch):
        monkeypatch.setattr(di, "MAX_IMAGE_BYTES", 50)
        session = FakeSession({
            self.sized: FakeResponse(body=b"x" * 5000),
            self.original: FakeResponse(body=b"x" * 5000),
        })
        assert di.fetch_thumbnail(session, self.original) is None

    def test_network_error_never_raises(self):
        session = FakeSession({
            self.sized: OSError("connection reset"),
            self.original: OSError("connection reset"),
        })
        assert di.fetch_thumbnail(session, self.original) is None

    def test_empty_url(self):
        assert di.fetch_thumbnail(FakeSession({}), "") is None


class TestFetchThumbnails:
    def _session(self, n):
        responses = {}
        for i in range(n):
            responses[di.thumbnail_url(f"https://static-01.daraz.com.np/p/{i}.jpg")] = (
                FakeResponse(content_type="image/webp")
            )
        return FakeSession(responses)

    def test_respects_the_limit(self):
        urls = [f"https://static-01.daraz.com.np/p/{i}.jpg" for i in range(5)]
        out = di.fetch_thumbnails(self._session(5), urls, limit=2)
        assert len(out) == 2

    def test_deduplicates(self):
        url = "https://static-01.daraz.com.np/p/0.jpg"
        out = di.fetch_thumbnails(self._session(1), [url, url, url], limit=5)
        assert len(out) == 1

    def test_failures_are_simply_absent(self):
        good = "https://static-01.daraz.com.np/p/0.jpg"
        bad = "https://static-01.daraz.com.np/p/9.jpg"
        session = FakeSession({
            di.thumbnail_url(good): FakeResponse(content_type="image/webp"),
            di.thumbnail_url(bad): FakeResponse(status=500),
            bad: FakeResponse(status=500),
        })
        out = di.fetch_thumbnails(session, [good, bad], limit=5)
        assert list(out) == [good]

    def test_limit_of_zero_fetches_nothing(self):
        out = di.fetch_thumbnails(self._session(3), ["https://static-01.daraz.com.np/p/0.jpg"], limit=0)
        assert out == {}


class TestSearchWithImages:
    """A broken picture must never cost you a search result."""

    def _products(self):
        return [
            srv.Product(name="A", url="https://www.daraz.com.np/products/a.html",
                        price=100.0, image="https://static-01.daraz.com.np/p/a.jpg"),
            srv.Product(name="B", url="https://www.daraz.com.np/products/b.html",
                        price=200.0, image="https://static-01.daraz.com.np/p/b.jpg"),
        ]

    def test_text_survives_when_every_image_fails(self, monkeypatch):
        monkeypatch.setattr(di, "fetch_thumbnails", lambda *a, **k: {})
        blocks = srv._format_with_images(self._products(), "Results", "note", limit=8)
        text = "\n".join(b for b in blocks if isinstance(b, str))
        assert "**1. A**" in text and "**2. B**" in text
        assert "could not be loaded" in text

    def test_images_are_interleaved_after_their_product(self, monkeypatch):
        monkeypatch.setattr(
            di, "fetch_thumbnails",
            lambda session, urls, limit: {u: (b"bytes", "jpeg") for u in urls},
        )
        monkeypatch.setattr(di, "to_image_block", lambda data, fmt: {"image": fmt})
        blocks = srv._format_with_images(self._products(), "Results", "", limit=8)
        # header, text A, image A, text B, image B
        assert isinstance(blocks[0], str) and "Results" in blocks[0]
        assert "**1. A**" in blocks[1]
        assert blocks[2] == {"image": "jpeg"}
        assert "**2. B**" in blocks[3]
        assert blocks[4] == {"image": "jpeg"}

    def test_search_without_images_returns_plain_text(self, monkeypatch):
        monkeypatch.setattr(srv.daraz, "search", lambda q, **k: self._products())
        out = srv._search_daraz("mouse", include_images=False)
        assert isinstance(out, str)

    def test_search_with_images_returns_content_blocks(self, monkeypatch):
        monkeypatch.setattr(srv.daraz, "search", lambda q, **k: self._products())
        monkeypatch.setattr(
            di, "fetch_thumbnails",
            lambda session, urls, limit: {u: (b"bytes", "jpeg") for u in urls},
        )
        monkeypatch.setattr(di, "to_image_block", lambda data, fmt: {"image": fmt})
        out = srv._search_daraz("mouse", include_images=True)
        assert isinstance(out, list)
        assert any(b == {"image": "jpeg"} for b in out)
