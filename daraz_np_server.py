"""
Daraz Nepal MCP Server
======================

A Model Context Protocol server for searching products on Daraz Nepal
(https://www.daraz.com.np) using Daraz's public `ajax=true` catalog endpoints.

Tools exposed:
    * search_daraz     - search / filter / sort the catalog
    * product_details  - full detail for a single product URL
    * list_categories  - useful category slugs for focused searches

No seller account or API key is required; only public endpoints are used.

Environment variables:
    DARAZ_DOMAIN    default "www.daraz.com.np"  (e.g. www.daraz.com.bd, www.daraz.lk)
    DARAZ_CURRENCY  default "Rs."               (display prefix)
    DARAZ_TIMEOUT   default "20"                (seconds per HTTP request)
    DARAZ_LOG_FILE  optional path for a debug log file (off by default)
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from fastmcp import FastMCP

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DOMAIN = os.getenv("DARAZ_DOMAIN", "www.daraz.com.np").strip().strip("/")
BASE_URL = f"https://{DOMAIN}"
CURRENCY = os.getenv("DARAZ_CURRENCY", "Rs.")
TIMEOUT = float(os.getenv("DARAZ_TIMEOUT", "20"))
LOG_FILE = os.getenv("DARAZ_LOG_FILE", "").strip()

_handlers: List[logging.Handler] = [logging.StreamHandler()]
if LOG_FILE:
    _handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))

logging.basicConfig(
    level=os.getenv("DARAZ_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger("daraz-np")

mcp = FastMCP("Daraz Nepal")

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Sort keys accepted by the Daraz catalog endpoint.
SORT_MAP = {
    "relevance": None,
    "best_match": None,
    "price_low": "priceasc",
    "price_asc": "priceasc",
    "cheapest": "priceasc",
    "price_high": "pricedesc",
    "price_desc": "pricedesc",
    "popularity": "popularity",
    "best_selling": "popularity",
    "newest": "latest",
    "latest": "latest",
    "rating": "ratingscore",
}

# A handful of frequently useful Daraz Nepal category slugs.
CATEGORIES: Dict[str, str] = {
    "mobile-phones": "Mobile phones",
    "laptops": "Laptops",
    "tablets": "Tablets",
    "televisions": "Televisions",
    "computer-accessories": "Computer accessories",
    "audio": "Audio, headphones & speakers",
    "smart-watches": "Smart watches & wearables",
    "cameras": "Cameras",
    "home-appliances": "Home appliances",
    "kitchen-dining": "Kitchen & dining",
    "furniture-decor": "Furniture & decor",
    "mens-fashion": "Men's fashion",
    "womens-fashion": "Women's fashion",
    "shoes": "Shoes",
    "watches-sunglasses-jewellery": "Watches, sunglasses & jewellery",
    "health-beauty": "Health & beauty",
    "mother-baby": "Mother & baby",
    "groceries": "Groceries & pets",
    "sports-outdoors": "Sports & outdoors",
    "motors": "Automotive & motorbike",
    "books": "Books & stationery",
    "toys-games": "Toys & games",
}


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #


def _build_session() -> requests.Session:
    """A session with connection pooling, retries and exponential backoff."""
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=2,
        backoff_factor=0.8,
        status_forcelist=(408, 425, 429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _headers(ajax: bool = True, referer: Optional[str] = None) -> Dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*" if ajax else
                  "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ne;q=0.8",
        "Referer": referer or f"{BASE_URL}/",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty" if ajax else "document",
        "Sec-Fetch-Mode": "cors" if ajax else "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Upgrade-Insecure-Requests": "1",
    }


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass
class Product:
    name: str
    url: str
    price: Optional[float] = None
    original_price: Optional[float] = None
    discount: Optional[str] = None
    rating: Optional[float] = None
    reviews: Optional[int] = None
    sold: Optional[str] = None
    seller: Optional[str] = None
    location: Optional[str] = None
    brand: Optional[str] = None
    image: Optional[str] = None
    in_stock: Optional[bool] = None
    item_id: Optional[str] = None
    currency: str = "NPR"
    badges: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Scraper
# --------------------------------------------------------------------------- #


class DarazNepal:
    """Thin client over the public Daraz Nepal catalog endpoints."""

    def __init__(self) -> None:
        self.session = _build_session()
        self._warmed = False

    # -- helpers ---------------------------------------------------------- #

    def _warm_up(self) -> None:
        """Fetch the homepage once so we hold the anti-bot cookies."""
        if self._warmed:
            return
        try:
            self.session.get(BASE_URL, headers=_headers(ajax=False), timeout=TIMEOUT)
            logger.debug("Warm-up request completed (%d cookies)", len(self.session.cookies))
        except Exception as exc:  # noqa: BLE001 - warm-up is best effort
            logger.debug("Warm-up failed (continuing anyway): %s", exc)
        finally:
            self._warmed = True

    @staticmethod
    def parse_price(value: Any) -> Optional[float]:
        """'Rs. 1,234.00' / '1234.00' / 1234 -> 1234.0"""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value) or None
        text = str(value).strip()
        if not text:
            return None
        text = re.sub(r"(?i)\b(rs\.?|npr|nrs\.?|₨|rupees?)\b", "", text)
        text = text.replace(" ", " ")
        # Keep the first numeric token only ("Rs. 1,200 - Rs. 2,400" -> 1200)
        match = re.search(r"\d[\d,]*(?:\.\d+)?", text)
        if not match:
            return None
        try:
            price = float(match.group(0).replace(",", ""))
        except ValueError:
            return None
        return price if price > 0 else None

    @staticmethod
    def _abs_url(url: str) -> str:
        if not url:
            return ""
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            return BASE_URL + url
        if not url.startswith("http"):
            return f"{BASE_URL}/{url.lstrip('/')}"
        return url

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        match = re.search(r"\d[\d,]*", str(value))
        if not match:
            return None
        try:
            return int(match.group(0).replace(",", ""))
        except ValueError:
            return None

    # -- item normalisation ------------------------------------------------ #

    def _normalise(self, item: Dict[str, Any]) -> Optional[Product]:
        name = (
            item.get("name")
            or item.get("title")
            or item.get("productName")
            or ""
        ).strip()
        url = self._abs_url(
            item.get("productUrl")
            or item.get("itemUrl")
            or item.get("link")
            or item.get("url")
            or ""
        )
        if not name or not url:
            return None

        price = self.parse_price(
            item.get("price")
            or item.get("priceShow")
            or item.get("salePrice")
            or item.get("currentPrice")
        )
        original = self.parse_price(
            item.get("originalPrice")
            or item.get("originalPriceShow")
            or item.get("listPrice")
            or item.get("marketPrice")
        )
        if original and price and original <= price:
            original = None

        discount = item.get("discount") or None
        if not discount and original and price:
            discount = f"{round((original - price) / original * 100)}%"

        rating = None
        try:
            raw_rating = item.get("ratingScore")
            if raw_rating not in (None, "", "0", "0.0"):
                rating = round(float(raw_rating), 1)
        except (TypeError, ValueError):
            rating = None

        in_stock = item.get("inStock")
        if isinstance(in_stock, str):
            in_stock = in_stock.strip().lower() in ("true", "1", "yes")

        badges: List[str] = []
        for icon in item.get("icons") or []:
            label = (icon or {}).get("text") or (icon or {}).get("name")
            if label:
                badges.append(str(label))
        if item.get("isSponsored"):
            badges.append("Sponsored")

        return Product(
            name=name,
            url=url.split("?")[0],
            price=price,
            original_price=original,
            discount=discount,
            rating=rating,
            reviews=self._to_int(item.get("review") or item.get("reviewCount")),
            sold=item.get("itemSoldCntShow") or None,
            seller=item.get("sellerName") or None,
            location=item.get("location") or None,
            brand=item.get("brandName") or None,
            image=self._abs_url(item.get("image") or ""),
            in_stock=in_stock,
            item_id=str(item.get("itemId") or item.get("nid") or "") or None,
            currency=item.get("currency") or "NPR",
            badges=badges,
        )

    # -- catalog fetch ----------------------------------------------------- #

    def fetch_page(
        self,
        query: str,
        page: int = 1,
        category: Optional[str] = None,
        sort: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
    ) -> List[Product]:
        """One page of catalog results. Returns [] on any failure."""
        self._warm_up()

        if category:
            url = f"{BASE_URL}/{category.strip('/')}/"
            params: Dict[str, Any] = {"ajax": "true", "page": page}
            if query:
                params["q"] = query
        else:
            url = f"{BASE_URL}/catalog/"
            params = {"ajax": "true", "q": query, "page": page, "_keyori": "ss"}

        if sort:
            params["sort"] = sort
        if min_price is not None or max_price is not None:
            lo = int(min_price) if min_price is not None else 0
            hi = int(max_price) if max_price is not None else 9999999
            params["price"] = f"{lo}-{hi}"

        try:
            response = self.session.get(
                url, params=params, headers=_headers(), timeout=TIMEOUT
            )
            if response.status_code != 200:
                logger.warning("HTTP %s for %s", response.status_code, response.url)
                return []
            data = response.json()
        except json.JSONDecodeError:
            logger.warning("Non-JSON response (anti-bot page?) for page %s", page)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.error("Request failed on page %s: %s", page, exc)
            return []

        items = self._extract_items(data)
        if not items:
            logger.info("No items in response for page %s", page)
            return []

        products = [p for p in (self._normalise(i) for i in items) if p]
        logger.info("Page %s -> %s products", page, len(products))
        return products

    @staticmethod
    def _extract_items(data: Any) -> List[Dict[str, Any]]:
        """Daraz nests items differently across regions / A-B tests."""
        if not isinstance(data, dict):
            return []
        candidates = [
            ("mods", "listItems"),
            ("data", "mods", "listItems"),
            ("mods", "items"),
            ("listItems",),
            ("results",),
            ("data", "products"),
        ]
        for path in candidates:
            node: Any = data
            for key in path:
                if isinstance(node, dict) and key in node:
                    node = node[key]
                else:
                    node = None
                    break
            if isinstance(node, list) and node:
                return [i for i in node if isinstance(i, dict)]
        return []

    def search(
        self,
        query: str,
        limit: int = 10,
        pages: int = 3,
        category: Optional[str] = None,
        sort: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        in_stock_only: bool = False,
    ) -> List[Product]:
        collected: List[Product] = []
        seen: set[str] = set()

        for page in range(1, max(1, pages) + 1):
            batch = self.fetch_page(
                query, page, category=category, sort=sort,
                min_price=min_price, max_price=max_price,
            )
            if not batch:
                break

            for product in batch:
                key = product.item_id or product.url
                if key in seen:
                    continue
                if in_stock_only and product.in_stock is False:
                    continue
                # Server-side price filters are applied where supported; enforce
                # them client-side too because category pages ignore `price`.
                if min_price is not None and (product.price or 0) < min_price:
                    continue
                if max_price is not None and product.price is not None and product.price > max_price:
                    continue
                seen.add(key)
                collected.append(product)

            if len(collected) >= limit:
                break
            time.sleep(random.uniform(0.6, 1.4))

        return collected

    # -- product detail page ---------------------------------------------- #

    def product_detail(self, url: str) -> Dict[str, Any]:
        self._warm_up()
        url = self._abs_url(url).split("?")[0]

        response = self.session.get(
            url, headers=_headers(ajax=False, referer=f"{BASE_URL}/"), timeout=TIMEOUT
        )
        response.raise_for_status()
        html = response.text
        soup = BeautifulSoup(html, "html.parser")

        details: Dict[str, Any] = {"url": url}

        # 1. Daraz embeds the PDP payload in `window.__moduleData__`.
        module = self._extract_module_data(html)
        if module:
            fields = (
                module.get("data", {})
                .get("root", {})
                .get("fields", {})
            ) or module.get("fields", {}) or {}
            product = fields.get("product") or {}
            skus = product.get("skuInfos") or {}
            first_sku = {}
            if isinstance(skus, dict) and skus:
                first_sku = next(iter(skus.values())) or {}

            details["name"] = product.get("title") or details.get("name")
            details["brand"] = (product.get("brand") or {}).get("title")
            details["rating"] = (fields.get("review") or {}).get("ratings", {}).get("average")
            details["reviews"] = (fields.get("review") or {}).get("ratings", {}).get("total")
            details["seller"] = (fields.get("seller") or {}).get("name")
            details["seller_rating"] = (fields.get("seller") or {}).get("positiveRate")
            price_block = (first_sku.get("price") or {}).get("salePrice") or {}
            details["price"] = self.parse_price(price_block.get("text") or price_block.get("value"))
            origin_block = (first_sku.get("price") or {}).get("originalPrice") or {}
            details["original_price"] = self.parse_price(
                origin_block.get("text") or origin_block.get("value")
            )
            details["stock"] = first_sku.get("quantity")
            spec = product.get("desc") or product.get("highlights")
            if isinstance(spec, str):
                details["highlights"] = BeautifulSoup(spec, "html.parser").get_text(" ", strip=True)[:1200]

        # 2. JSON-LD fallback / top-up.
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                blob = json.loads(tag.string or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            nodes = blob if isinstance(blob, list) else [blob]
            for node in nodes:
                if not isinstance(node, dict) or node.get("@type") != "Product":
                    continue
                details.setdefault("name", node.get("name"))
                offers = node.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if not details.get("price"):
                    details["price"] = self.parse_price(offers.get("price"))
                details.setdefault("availability", str(offers.get("availability", "")).split("/")[-1])
                rating = node.get("aggregateRating") or {}
                if rating and not details.get("rating"):
                    details["rating"] = rating.get("ratingValue")
                    details["reviews"] = rating.get("reviewCount") or rating.get("ratingCount")
                details.setdefault("image", node.get("image"))
                if node.get("description") and not details.get("highlights"):
                    details["highlights"] = str(node["description"])[:1200]

        # 3. Meta / DOM fallback.
        if not details.get("name"):
            og_title = soup.find("meta", property="og:title")
            h1 = soup.find("h1")
            details["name"] = (
                (og_title.get("content") if og_title else None)
                or (h1.get_text(strip=True) if h1 else None)
                or "Unknown product"
            )
        if not details.get("price"):
            price_node = soup.find(class_=re.compile(r"pdp-price", re.I)) or soup.find(
                ["span", "div"], class_=re.compile(r"price", re.I)
            )
            if price_node:
                details["price"] = self.parse_price(price_node.get_text(strip=True))
        if not details.get("image"):
            og_image = soup.find("meta", property="og:image")
            if og_image:
                details["image"] = og_image.get("content")

        return {k: v for k, v in details.items() if v not in (None, "", [], {})}

    @staticmethod
    def _extract_module_data(html: str) -> Optional[Dict[str, Any]]:
        match = re.search(
            r"window\.__moduleData__\s*=\s*(\{.*?\});?\s*</script>", html, re.S
        )
        if not match:
            match = re.search(r"var\s+__moduleData__\s*=\s*(\{.*?\});", html, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None

    # -- Playwright fallback ---------------------------------------------- #

    def browser_search(self, query: str, page: int = 1) -> List[Product]:
        """Render the search page with Playwright when the JSON API is blocked."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.info("Playwright not installed - skipping browser fallback")
            return []

        results: List[Product] = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(user_agent=random.choice(USER_AGENTS))
                tab = context.new_page()
                tab.goto(
                    f"{BASE_URL}/catalog/?q={requests.utils.quote(query)}&page={page}",
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
                tab.wait_for_timeout(2500)
                cards = tab.query_selector_all('[data-qa-locator="product-item"]')
                for card in cards:
                    soup = BeautifulSoup(card.inner_html(), "html.parser")
                    link = soup.find("a", href=True)
                    title = soup.find(attrs={"title": True})
                    name = (
                        title["title"].strip()
                        if title
                        else (link.get_text(strip=True) if link else "")
                    )
                    price_node = soup.find(class_=re.compile(r"currency|price", re.I))
                    if not (name and link):
                        continue
                    results.append(
                        Product(
                            name=name,
                            url=self._abs_url(link["href"]).split("?")[0],
                            price=self.parse_price(
                                price_node.get_text(strip=True) if price_node else ""
                            ),
                            image=self._abs_url(
                                (soup.find("img") or {}).get("src", "")
                                if soup.find("img")
                                else ""
                            ),
                        )
                    )
                browser.close()
        except Exception as exc:  # noqa: BLE001
            logger.error("Browser fallback failed: %s", exc)
        logger.info("Browser fallback -> %s products", len(results))
        return results


daraz = DarazNepal()


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #


def _money(value: Optional[float]) -> str:
    return f"{CURRENCY} {value:,.0f}" if value is not None else "price unavailable"


def _format_products(products: List[Product], header: str, note: str = "") -> str:
    lines = [f"**{header}**", ""]
    for index, p in enumerate(products, 1):
        lines.append(f"**{index}. {p.name}**")

        if p.original_price and p.price:
            saving = p.original_price - p.price
            lines.append(
                f"   Price: **{_money(p.price)}**  ~~{_money(p.original_price)}~~"
                f"  ({p.discount or ''} off, saves {_money(saving)})".replace("( off", "(")
            )
        else:
            lines.append(f"   Price: **{_money(p.price)}**")

        meta = []
        if p.rating:
            meta.append(f"{p.rating}/5" + (f" ({p.reviews} reviews)" if p.reviews else ""))
        if p.sold:
            meta.append(str(p.sold))
        if p.brand:
            meta.append(f"Brand: {p.brand}")
        if p.location:
            meta.append(p.location)
        if p.in_stock is False:
            meta.append("Out of stock")
        if p.badges:
            meta.append(", ".join(p.badges[:3]))
        if meta:
            lines.append("   " + " · ".join(meta))

        if p.seller:
            lines.append(f"   Seller: {p.seller}")
        lines.append(f"   {p.url}")
        lines.append("")

    if note:
        lines.append(note)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# MCP tools
# --------------------------------------------------------------------------- #


def _search_daraz(
    query: str,
    limit: int = 10,
    sort: str = "relevance",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    category: Optional[str] = None,
    in_stock_only: bool = False,
    pages: int = 3,
    as_json: bool = False,
) -> str:
    """
    Search Daraz Nepal (daraz.com.np) for products. Prices are in NPR.

    Args:
        query: What to search for, e.g. "wireless mouse", "gas stove", "iPhone 15 case".
        limit: Number of products to return (default 10, max 50).
        sort: One of relevance, price_low, price_high, popularity, newest, rating.
              Use "price_low" for cheapest-first questions.
        min_price: Minimum price in NPR (optional).
        max_price: Maximum price in NPR (optional).
        category: Optional category slug to search within, e.g. "mobile-phones".
                  Call list_categories() to see common slugs.
        in_stock_only: Drop items Daraz reports as out of stock.
        pages: How many result pages to pull (1-10). More pages = slower.
        as_json: Return structured JSON instead of formatted text.

    Returns:
        Formatted product list (name, price, discount, rating, seller, link),
        or a JSON array when as_json=True.
    """
    query = (query or "").strip()
    if not query and not category:
        return "Provide a search query, or a category slug to browse."

    limit = max(1, min(int(limit), 50))
    pages = max(1, min(int(pages), 10))

    # Natural-language shortcuts the model may not translate into params.
    lowered = query.lower()
    if sort in ("relevance", "best_match") and re.search(r"\b(cheap|cheapest|lowest price)\b", lowered):
        sort = "price_low"
    if sort in ("relevance", "best_match") and re.search(r"\b(most expensive|premium|highest price)\b", lowered):
        sort = "price_high"

    sort_key = SORT_MAP.get(sort.lower().replace(" ", "_"), None)
    if sort.lower() not in SORT_MAP:
        logger.info("Unknown sort '%s' - falling back to relevance", sort)

    products = daraz.search(
        query,
        limit=limit,
        pages=pages,
        category=category,
        sort=sort_key,
        min_price=min_price,
        max_price=max_price,
        in_stock_only=in_stock_only,
    )

    if not products:
        logger.info("JSON API returned nothing - trying browser fallback")
        products = daraz.browser_search(query)
        if max_price is not None:
            products = [p for p in products if p.price is not None and p.price <= max_price]

    if not products:
        bounds = ""
        if min_price is not None or max_price is not None:
            bounds = f" between {_money(min_price or 0)} and {_money(max_price)}"
        return (
            f"No products found on Daraz Nepal for '{query}'{bounds}.\n"
            "Try a shorter or more common search term, or widen the price range."
        )

    # Client-side sort guarantees the requested order even if Daraz ignores it.
    if sort_key == "priceasc":
        products.sort(key=lambda p: (p.price is None, p.price or 0))
    elif sort_key == "pricedesc":
        products.sort(key=lambda p: (p.price is None, -(p.price or 0)))

    products = products[:limit]

    if as_json:
        return json.dumps([p.as_dict() for p in products], indent=2, ensure_ascii=False)

    label = query.strip() or (category or "").replace("-", " ")
    if sort_key == "priceasc":
        header = f"Cheapest {label} on Daraz Nepal ({len(products)} shown)"
    elif sort_key == "pricedesc":
        header = f"Most expensive {label} on Daraz Nepal ({len(products)} shown)"
    else:
        header = f"{len(products)} results for '{label}' on Daraz Nepal"
    if category:
        header += f" · category: {category}"

    note = (
        "Prices are live from daraz.com.np and can change; check the product page "
        "for final price, delivery cost and seller rating."
    )
    return _format_products(products, header, note)


def _product_details(url: str) -> str:
    """
    Fetch full details for one Daraz Nepal product page.

    Args:
        url: A daraz.com.np product URL (as returned by search_daraz).

    Returns:
        Name, price, discount, rating, review count, seller, availability and
        a short description when Daraz exposes them.
    """
    if not url or "daraz." not in url:
        return "Please pass a full Daraz product URL, e.g. https://www.daraz.com.np/products/...html"

    try:
        data = daraz.product_detail(url)
    except Exception as exc:  # noqa: BLE001
        return f"Could not load that product page: {exc}"

    lines = [f"**{data.get('name', 'Product')}**", ""]
    if data.get("price") is not None:
        if data.get("original_price"):
            lines.append(
                f"Price: **{_money(data['price'])}**  ~~{_money(data['original_price'])}~~"
            )
        else:
            lines.append(f"Price: **{_money(data['price'])}**")
    if data.get("brand"):
        lines.append(f"Brand: {data['brand']}")
    if data.get("rating"):
        reviews = f" from {data['reviews']} reviews" if data.get("reviews") else ""
        lines.append(f"Rating: {data['rating']}/5{reviews}")
    if data.get("seller"):
        rate = f" ({data['seller_rating']} positive)" if data.get("seller_rating") else ""
        lines.append(f"Seller: {data['seller']}{rate}")
    if data.get("stock") is not None:
        lines.append(f"Stock: {data['stock']}")
    elif data.get("availability"):
        lines.append(f"Availability: {data['availability']}")
    if data.get("highlights"):
        lines.append("")
        lines.append(data["highlights"])
    lines.append("")
    lines.append(data["url"])
    return "\n".join(lines)


def _list_categories() -> str:
    """
    List common Daraz Nepal category slugs that can be passed to
    search_daraz(category=...) for a focused search.
    """
    lines = ["**Daraz Nepal category slugs**", ""]
    for slug, label in CATEGORIES.items():
        lines.append(f"- `{slug}` — {label}")
    lines.append("")
    lines.append(
        "Any slug from a daraz.com.np category URL works, e.g. "
        "https://www.daraz.com.np/smartphones/ -> `smartphones`."
    )
    return "\n".join(lines)


# Register the tools. Implementations stay callable as plain functions so the
# CLI harness and tests can use them without an MCP client.
search_daraz = mcp.tool(name="search_daraz")(_search_daraz)
product_details = mcp.tool(name="product_details")(_product_details)
list_categories = mcp.tool(name="list_categories")(_list_categories)


if __name__ == "__main__":
    logger.info("Starting Daraz MCP server against %s", BASE_URL)
    mcp.run()
