# Daraz Nepal MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-server-6E56CF.svg)](https://modelcontextprotocol.io)

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant search
**Daraz Nepal** (https://www.daraz.com.np) — live prices in NPR, no API key, no
seller account. Point it at any Daraz region with one environment variable.

Ask your assistant *"find me a wireless mouse under Rs. 1500"* and it searches,
filters and sorts real listings.

```
search_daraz("wireless mouse", max_price=1500, sort="price_low")

1. Logitech M170 Wireless Mouse — Rs. 1,299  (was Rs. 1,799, -28%)
   ★ 4.6 (1,204 reviews) · 3.2K sold · Logitech Official Store · Kathmandu
   https://www.daraz.com.np/products/...
```

---

## Requirements

- **Python 3.10 or newer**
- No API key, no Daraz account
- Optional: [Playwright](https://playwright.dev/python/) Chromium, used only as a
  fallback if Daraz's JSON endpoint gets blocked

## Install

```bash
git clone https://github.com/MaheshPhuyal02/daraz-np-mcp.git
cd daraz-np-mcp

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Optional browser fallback:

```bash
playwright install chromium
```

<details>
<summary>Using <a href="https://docs.astral.sh/uv/">uv</a> instead</summary>

```bash
uv venv
uv pip install -r requirements.txt
```

Then use `.venv/bin/python` (or `uv run python`) everywhere below.
</details>

## Verify it works

Before wiring it into an MCP client, check the scraper against the live site:

```bash
python daraz_cli.py doctor
```

Expected output ends with `All good — the MCP server should work.` If it doesn't,
see [Troubleshooting](#troubleshooting).

More CLI examples:

```bash
python daraz_cli.py search "wireless mouse" --limit 5
python daraz_cli.py search "gas stove" --sort price_low --max-price 8000
python daraz_cli.py search "headphones" --ships-from nepal
python daraz_cli.py search "tv" --category televisions --json
python daraz_cli.py details "https://www.daraz.com.np/products/....html"
python daraz_cli.py reviews "https://www.daraz.com.np/products/....html"
python daraz_cli.py categories
```

Run the offline unit tests (no network needed):

```bash
pytest -q
```

## Run the server

```bash
python daraz_np_server.py
```

It speaks MCP over stdio, so on its own it will just sit there waiting for a
client — that is expected. Normally you don't run it by hand; your MCP client
launches it for you, as configured below.

## Register with an MCP client

MCP clients do **not** inherit your shell's working directory or virtualenv, so
every path below must be **absolute**. Get yours with:

```bash
cd daraz-np-mcp
echo "$PWD/.venv/bin/python"        # Windows: echo %CD%\.venv\Scripts\python.exe
echo "$PWD/daraz_np_server.py"
```

### Claude Desktop

Edit the config file:

- **macOS** — `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows** — `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux** — `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "daraz-np": {
      "command": "/absolute/path/to/daraz-np-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/daraz-np-mcp/daraz_np_server.py"],
      "env": {
        "DARAZ_DOMAIN": "www.daraz.com.np",
        "DARAZ_CURRENCY": "Rs."
      }
    }
  }
}
```

Restart Claude Desktop, then look for the tools under the 🔌 / tools menu.
`mcp.example.json` in this repo has the same shape — copy it as a starting point.

### Claude Code

```bash
claude mcp add daraz-np \
  -e DARAZ_DOMAIN=www.daraz.com.np \
  -- /absolute/path/to/daraz-np-mcp/.venv/bin/python \
     /absolute/path/to/daraz-np-mcp/daraz_np_server.py
```

Check it registered with `claude mcp list`.

### Cursor / Windsurf / other clients

Anything that speaks MCP over stdio works — use the same `command` + `args`
shape as the Claude Desktop block above, in that client's MCP config
(`~/.cursor/mcp.json` for Cursor).

## Tools

### `search_daraz`

| Argument | Type | Default | Notes |
|---|---|---|---|
| `query` | str | — | e.g. `"wireless mouse"`, `"gas stove"` |
| `limit` | int | 10 | 1–50 results |
| `sort` | str | `relevance` | `price_low`, `price_high`, `popularity`, `newest`, `rating` |
| `min_price` / `max_price` | float | — | NPR, pushed to Daraz's own filter |
| `category` | str | — | slug, e.g. `mobile-phones` (see `list_categories`) |
| `in_stock_only` | bool | false | drop out-of-stock items |
| `ships_from` | str | `any` | `nepal` for local stock only, `overseas` for imports only |
| `include_images` | bool | false | attach product photos (see [Product images](#product-images)) |
| `pages` | int | 3 | result pages to pull, 1–10 |
| `as_json` | bool | false | structured output instead of markdown |

Queries containing "cheapest"/"cheap" auto-switch to `price_low`; "most
expensive"/"premium" to `price_high`.

### `product_details(url, include_image=False)`

Name, price, original price, brand, rating, review count, seller (+ positive
rating), stock and a trimmed description for a single product page. Pass
`include_image=True` to get the photo alongside the text.

### `product_reviews(url, ...)`

What buyers actually say about a product: the star breakdown, their written reviews, and a separate section of 1-2 star complaints.
The complaints are fetched on their own because Daraz's default ordering buries them.

| Arg | Type | Default | Notes |
|---|---|---|---|
| `url` | str | — | product URL from `search_daraz` |
| `limit` | int | 10 | written reviews to return, 1–50 |
| `stars` | int | — | only reviews with this rating, 1–5 |
| `sort` | str | `relevant` | or `recent` |
| `include_images` | bool | false | attach the photos buyers uploaded |
| `as_json` | bool | false | structured output instead of markdown |

Rating-only entries with no text are skipped, and each review shows its date, variant, whether it was a verified purchase and the seller's reply.
Reviews come from Daraz's public review endpoint, so no login is needed.

```bash
python daraz_cli.py reviews "https://www.daraz.com.np/products/....html"
python daraz_cli.py reviews "https://www.daraz.com.np/products/....html" --stars 1 --sort recent
```

### `list_categories()`

Common Daraz Nepal category slugs for focused searches.

### Where things ship from

Daraz Nepal lists imports next to local stock. They are usually cheaper and
usually much slower — two to four weeks is normal — so it matters which you're
looking at.

Every result is tagged. Anything shipping from abroad is marked **Overseas** in
the output, and `ships_from` narrows the search:

```bash
python daraz_cli.py search "mechanical keyboard" --ships-from nepal
```

The classification reads the seller's despatch location: a recognised Nepali
province or city is domestic, an explicit import marker (`Overseas`, `China`,
`Global`, …) anywhere in the location, badges or seller name is overseas, and
any *other* non-empty location is treated as overseas too. When Daraz gives no
location at all the item is `unknown` — and a `ships_from` filter never hides
those, because hiding a result on a guess is worse than showing one extra.

`ships_from` is also passed to Daraz's own location facet, so most of the work
happens server-side; the client-side filter is the guarantee for the regions
that ignore it.

### Product images

Text-only search tells you a mouse costs Rs. 1,299. It doesn't tell you it's
lime green. Pass `include_images=True` and each result comes back with its
photo attached, interleaved so every picture follows its own product:

```
search_daraz("office chair", include_images=True)
```

Images are **off by default**, because they cost context and add a round-trip
per picture — the tool description tells the assistant to switch them on for
"show me" questions and leave them off for price checks.

Costs are capped, in this order: Daraz's CDN resizes to a ~300px thumbnail
before anything is downloaded, at most `DARAZ_MAX_IMAGES` are fetched, and any
single image over `DARAZ_MAX_IMAGE_BYTES` is dropped mid-download rather than
sent. A picture that fails to load never costs you the result — the text entry
is always there, and the reply says how many images were missing.

The CLI is text-only, so `--json` and plain search print a note where images
would be. They render in an MCP client.

## Configuration

All optional — set them in the `env` block of your MCP client config.

| Env var | Default | Purpose |
|---|---|---|
| `DARAZ_DOMAIN` | `www.daraz.com.np` | point at another Daraz region (`www.daraz.com.bd`, `www.daraz.lk`, `www.daraz.com.mm`) |
| `DARAZ_CURRENCY` | `Rs.` | display prefix |
| `DARAZ_TIMEOUT` | `20` | seconds per HTTP request |
| `DARAZ_LOG_FILE` | unset | write a debug log to this path |
| `DARAZ_LOG_LEVEL` | `INFO` | `DEBUG` for verbose tracing |
| `DARAZ_MAX_IMAGES` | `8` | most product photos attached to one reply |
| `DARAZ_MAX_IMAGE_BYTES` | `409600` | drop any single image larger than this |
| `DARAZ_THUMB_SIZE` | `300` | thumbnail edge in px requested from Daraz's CDN |
| `DARAZ_THUMB_QUALITY` | `80` | thumbnail JPEG quality requested from the CDN |
| `DARAZ_IMAGE_TIMEOUT` | `10` | seconds per image fetch |

## How it works

Daraz's storefront renders from a JSON payload that the same URL returns when
you append `ajax=true`:

```
GET https://www.daraz.com.np/catalog/?ajax=true&q=mouse&page=1&_keyori=ss
GET https://www.daraz.com.np/{category}/?ajax=true&page=1
```

Products live at `mods.listItems` (the client also probes `data.mods.listItems`,
`listItems`, `results` and `data.products` since Daraz A/B-tests its response
shape). Sorting, price bounds and despatch location are passed as `sort=`,
`price=min-max` and `location=` so Daraz does the work server-side; the result
is re-sorted and re-filtered locally as a guard.

Product photos come from the same CDN the storefront uses. Appending
`_300x300q80.jpg_.webp` to an image path makes Daraz return a thumbnail, so the
server never downloads a full-size original just to shrink it.

If the JSON endpoint returns HTML (anti-bot page), the server falls back to
rendering the search page with Playwright — install it or that step is skipped
with a log line.

## Troubleshooting

**`doctor` says "response was not JSON (likely an anti-bot / captcha page)"**
Daraz is rate-limiting or challenging you. Wait a few minutes, and install the
Playwright fallback (`playwright install chromium`).

**`doctor` parses 0 items**
Daraz probably changed its response shape. `doctor` prints the raw item keys and
`mods` keys it received — that's the first place to look, and a great PR.

**Tools don't show up in the client**
Almost always a path problem. Use absolute paths, point `command` at the
**venv's** Python (not system `python3`), and check the client's MCP log. Running
the same command by hand in a terminal should hang silently rather than error.

**`ModuleNotFoundError: fastmcp`**
The client is using a Python without the dependencies installed — see above.

**`ships_from="nepal"` still shows an overseas item**
Its despatch location was blank, so the item is classified `unknown` and the
filter deliberately leaves it in rather than hiding a result on a guess. If
Daraz is showing a location the classifier does not recognise, add it to
`NEPAL_LOCATIONS` or `OVERSEAS_MARKERS` in `daraz_np_server.py`.

**`include_images=True` returns text but no pictures**
The reply says how many images failed. Most often Daraz's CDN refused the
resized URL and the original was over `DARAZ_MAX_IMAGE_BYTES` — raise that, or
lower `DARAZ_THUMB_SIZE`. Run with `DARAZ_LOG_LEVEL=DEBUG` to see each fetch.

## Notes and limits

- Search uses public endpoints only; no seller API. Daraz can change or throttle
  them at any time — `python daraz_cli.py doctor` tells you which.
- Requests are paced 0.6–1.4 s apart with a rotating User-Agent. Don't raise
  `pages` far beyond the default if you're running many searches.
- Prices are live at fetch time; always confirm on the product page before buying.
- Scraping is best-effort and for personal use. Check Daraz's terms of service
  before running this at volume. This project is not affiliated with, endorsed
  by, or connected to Daraz or Alibaba Group.

## Contributing

Bug reports and PRs are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © MaheshPhuyal02.
