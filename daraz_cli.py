#!/usr/bin/env python3
"""
Command-line harness for the Daraz Nepal MCP server.

Lets you verify the scraping layer without wiring up an MCP client.

    python daraz_cli.py search "wireless mouse"
    python daraz_cli.py search "gas stove" --sort price_low --max-price 8000 --limit 5
    python daraz_cli.py search "tv" --category televisions --json
    python daraz_cli.py search "headphones" --ships-from nepal
    python daraz_cli.py details "https://www.daraz.com.np/products/....html"
    python daraz_cli.py reviews "https://www.daraz.com.np/products/....html" --stars 1
    python daraz_cli.py categories
    python daraz_cli.py doctor        # connectivity + endpoint health check
"""

from __future__ import annotations

import argparse
import json
import sys

import daraz_np_server as srv


def cmd_search(args: argparse.Namespace) -> int:
    out = srv._search_daraz(
        query=args.query,
        limit=args.limit,
        sort=args.sort,
        min_price=args.min_price,
        max_price=args.max_price,
        category=args.category,
        in_stock_only=args.in_stock,
        ships_from=args.ships_from,
        pages=args.pages,
        as_json=args.json,
    )
    # With images the tool returns content blocks; the CLI can only show text.
    if isinstance(out, list):
        pictures = 0
        for block in out:
            if isinstance(block, str):
                print(block)
            else:
                pictures += 1
        print(f"\n[{pictures} image(s) omitted — the CLI is text only; "
              "they show up in an MCP client]")
    else:
        print(out)
    return 0


def cmd_details(args: argparse.Namespace) -> int:
    print(srv._product_details(url=args.url))
    return 0


def cmd_reviews(args: argparse.Namespace) -> int:
    print(srv._product_reviews(
        url=args.url, limit=args.limit, stars=args.stars, sort=args.sort, as_json=args.json,
    ))
    return 0


def cmd_categories(_: argparse.Namespace) -> int:
    print(srv._list_categories())
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    print(f"Target domain : {srv.BASE_URL}")
    session = srv.daraz.session

    try:
        home = session.get(srv.BASE_URL, headers=srv._headers(ajax=False), timeout=srv.TIMEOUT)
        print(f"Homepage      : HTTP {home.status_code}, {len(session.cookies)} cookies")
    except Exception as exc:  # noqa: BLE001
        print(f"Homepage      : FAILED - {exc}")
        return 1

    try:
        resp = session.get(
            f"{srv.BASE_URL}/catalog/",
            params={"ajax": "true", "q": "mouse", "page": 1, "_keyori": "ss"},
            headers=srv._headers(),
            timeout=srv.TIMEOUT,
        )
        print(f"Catalog ajax  : HTTP {resp.status_code}, {len(resp.content):,} bytes")
        data = resp.json()
        items = srv.DarazNepal._extract_items(data)
        print(f"Items parsed  : {len(items)}")
        if items:
            product = srv.daraz._normalise(items[0])
            print("Sample item   :")
            print(json.dumps(product.as_dict() if product else {}, indent=2)[:900])
            print("\nTop-level keys:", list(data.keys())[:10])
            print("mods keys     :", list(data.get("mods", {}).keys())[:12])
            print("Raw item keys :", sorted(items[0].keys()))
        else:
            print("No items found. Response preview:")
            print(resp.text[:400])
            return 1
    except json.JSONDecodeError:
        print("Catalog ajax  : response was not JSON (likely an anti-bot / captcha page)")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Catalog ajax  : FAILED - {exc}")
        return 1

    print("\nAll good — the MCP server should work.")
    return 0



def main() -> int:
    parser = argparse.ArgumentParser(description="Daraz Nepal CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="search the catalog")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--pages", type=int, default=3)
    s.add_argument(
        "--sort",
        default="relevance",
        choices=sorted(srv.SORT_MAP.keys()),
    )
    s.add_argument("--min-price", type=float, default=None)
    s.add_argument("--max-price", type=float, default=None)
    s.add_argument("--category", default=None)
    s.add_argument("--in-stock", action="store_true")
    s.add_argument(
        "--ships-from", default="any", choices=list(srv.SHIPS_FROM_CHOICES),
        help="filter by despatch origin (default: any)",
    )
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_search)

    d = sub.add_parser("details", help="fetch one product page")
    d.add_argument("url")
    d.set_defaults(func=cmd_details)

    rv = sub.add_parser("reviews", help="buyer reviews and rating breakdown for a product")
    rv.add_argument("url")
    rv.add_argument("--limit", type=int, default=10)
    rv.add_argument("--stars", type=int, choices=range(1, 6), default=None)
    rv.add_argument("--sort", default="relevant", choices=list(srv.REVIEW_SORTS))
    rv.add_argument("--json", action="store_true")
    rv.set_defaults(func=cmd_reviews)

    c = sub.add_parser("categories", help="list category slugs")
    c.set_defaults(func=cmd_categories)

    doc = sub.add_parser("doctor", help="check connectivity and endpoint health")
    doc.set_defaults(func=cmd_doctor)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
