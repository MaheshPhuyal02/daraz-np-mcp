"""
Offline tests — no network required.

    pip install pytest
    pytest -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import daraz_np_server as srv  # noqa: E402

D = srv.DarazNepal


class TestParsePrice:
    def test_plain_number(self):
        assert D.parse_price("1234") == 1234.0

    def test_rupee_prefix_and_commas(self):
        assert D.parse_price("Rs. 1,234.00") == 1234.0
        assert D.parse_price("NPR 12,500") == 12500.0
        assert D.parse_price("Nrs. 899") == 899.0

    def test_price_range_takes_lowest_shown(self):
        assert D.parse_price("Rs. 1,200 - Rs. 2,400") == 1200.0

    def test_numeric_input(self):
        assert D.parse_price(2999) == 2999.0
        assert D.parse_price(2999.5) == 2999.5

    def test_empty_and_zero(self):
        assert D.parse_price("") is None
        assert D.parse_price(None) is None
        assert D.parse_price("Rs. 0") is None
        assert D.parse_price("free shipping") is None


class TestExtractItems:
    def test_documented_path(self):
        data = {"mods": {"listItems": [{"name": "x"}]}}
        assert D._extract_items(data) == [{"name": "x"}]

    def test_nested_data_path(self):
        data = {"data": {"mods": {"listItems": [{"name": "y"}]}}}
        assert D._extract_items(data) == [{"name": "y"}]

    def test_empty_and_garbage(self):
        assert D._extract_items({}) == []
        assert D._extract_items({"mods": {"listItems": []}}) == []
        assert D._extract_items("not a dict") == []


class TestNormalise:
    raw = {
        "name": "Logitech M170 Wireless Mouse",
        "productUrl": "//www.daraz.com.np/products/logitech-m170-i123.html?spm=abc",
        "price": "1450.00",
        "originalPrice": "1990.00",
        "discount": "27%",
        "ratingScore": "4.6",
        "review": "1,205",
        "sellerName": "Logitech Official Store",
        "location": "Kathmandu",
        "brandName": "Logitech",
        "image": "//static.daraz.com.np/p/abc.jpg",
        "inStock": "true",
        "itemId": "123456",
        "itemSoldCntShow": "2.1K sold",
    }

    def test_full_mapping(self):
        p = srv.daraz._normalise(self.raw)
        assert p is not None
        assert p.name.startswith("Logitech M170")
        assert p.url == "https://www.daraz.com.np/products/logitech-m170-i123.html"
        assert p.price == 1450.0
        assert p.original_price == 1990.0
        assert p.rating == 4.6
        assert p.reviews == 1205
        assert p.in_stock is True
        assert p.brand == "Logitech"
        assert p.image.startswith("https://")

    def test_discount_computed_when_missing(self):
        raw = dict(self.raw)
        raw.pop("discount")
        p = srv.daraz._normalise(raw)
        assert p.discount == "27%"

    def test_bogus_original_price_dropped(self):
        raw = dict(self.raw, originalPrice="1000.00")  # lower than sale price
        p = srv.daraz._normalise(raw)
        assert p.original_price is None

    def test_missing_url_rejected(self):
        assert srv.daraz._normalise({"name": "no url"}) is None
        assert srv.daraz._normalise({"productUrl": "/x"}) is None

    def test_item_url_alias(self):
        p = srv.daraz._normalise({"name": "a", "itemUrl": "/products/a.html", "price": "5"})
        assert p.url == "https://www.daraz.com.np/products/a.html"


class TestFormatting:
    def test_money_uses_configured_currency(self):
        assert srv._money(1234.0) == f"{srv.CURRENCY} 1,234"
        assert srv._money(None) == "price unavailable"

    def test_format_products_contains_link_and_price(self):
        p = srv.Product(name="Test", url="https://www.daraz.com.np/products/t.html", price=999.0)
        out = srv._format_products([p], "Header")
        assert "Test" in out and "999" in out and p.url in out


class TestToolWiring:
    def test_defaults_target_nepal(self):
        assert srv.BASE_URL == "https://www.daraz.com.np"

    def test_sort_aliases(self):
        assert srv.SORT_MAP["cheapest"] == "priceasc"
        assert srv.SORT_MAP["price_high"] == "pricedesc"

    def test_empty_query_is_rejected(self):
        assert "Provide a search query" in srv._search_daraz("")

    def test_bad_product_url_is_rejected(self):
        assert "full Daraz product URL" in srv._product_details("https://example.com/x")

    def test_categories_listed(self):
        out = srv._list_categories()
        assert "mobile-phones" in out and "televisions" in out
