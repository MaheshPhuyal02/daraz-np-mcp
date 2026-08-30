# Contributing

Thanks for taking a look. This is a small project — issues and PRs are both
welcome, and you don't need to ask before opening either.

## Getting set up

```bash
git clone https://github.com/<your-fork>/daraz-np-mcp.git
cd daraz-np-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Sanity-check the scraper against the live site:

```bash
python daraz_cli.py doctor
```

## Before you open a PR

```bash
pytest -q                                    # offline unit tests, no network
python daraz_cli.py search "mouse" --limit 3 # live smoke test
python daraz_cli.py doctor
```

Please add a test for any parsing change. The suite in `tests/` is deliberately
offline — it feeds fixed dicts into the parsers rather than hitting Daraz, so it
stays fast and doesn't break when the site is down.

## What's most useful

- **Daraz changed its response shape.** The most likely kind of breakage. Run
  `doctor` — it prints the raw item keys and `mods` keys Daraz returned. Add the
  new path to `DarazNepal._extract_items` / `_normalise` plus a test case.
- **Other Daraz regions.** The server already targets any region via
  `DARAZ_DOMAIN`, but currency handling, category slugs and price formats vary.
  Fixes for `.com.bd`, `.lk`, `.com.mm` are welcome.
- **New product fields** exposed by the catalog payload.
- **Docs.** If a setup step tripped you up, that's a bug in the README.

## Code style

- Python 3.10+, type hints on public functions, `from __future__ import annotations`.
- Standard library and `requests`/`bs4`/`fastmcp` only — please don't add a
  dependency without a reason in the PR description.
- Keep tool implementations as plain functions (`_search_daraz`, …) registered
  with `mcp.tool(...)` at the bottom of the module, so the CLI and tests can call
  them without an MCP client.
- No secrets, no personal absolute paths, no committed log files.

## Scraping etiquette

This project only touches public endpoints, paces its requests and rotates a
User-Agent. Please don't send PRs that strip the delays, add aggressive
parallelism, or work around anti-bot measures beyond the existing Playwright
fallback.

## Reporting a bug

Include: your OS and Python version, the exact command, the full output of
`python daraz_cli.py doctor`, and what you expected instead.
