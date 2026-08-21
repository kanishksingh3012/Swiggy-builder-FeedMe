# feedme

Zero-phone food ordering from the terminal, via Swiggy's MCP servers. Search,
cart, coupon-optimize, and check out without touching your phone — no
QR-scan or UPI-PIN mobile handoff, ever.

> **Verification status (2026-08-21):** the wire protocol, argument casing,
> and most tool response shapes below are now **live-verified** against the
> real server — not the doc-derived guesses this started as. See each
> module's docstring for exactly what was confirmed and how (`mcp_client.py`
> for the protocol, `search.py`/`cart.py`/`checkout.py`/`tracking.py` for
> per-tool findings). Models stay permissive (`extra="allow"`) regardless,
> since the server itself mixes camelCase and snake_case field names in the
> same object.
>
> **Still unverified:** `place_food_order` itself (never called — real
> money), the shape of an *active* order's tracking data (no in-flight order
> existed to test against), and `report_error`. Everything else in the
> 14-tool Food list has been exercised live at least once.

## Install

```bash
pip install -e ".[dev]"
```

## Quickstart

```bash
python -m feedme.auth login
feedme "chicken bowl" --max-price 400 --fastest
```

The CLI will show matching results and **wait for you to pick one** — it
never adds anything to your cart on its own. If more than one zero-phone
payment method is available, it asks you to pick that too; if there's only
one (the common case today — Swiggy Money isn't always offered), it's used
without an extra prompt since there's no real choice to confirm.

## Architecture

```
CLI flags/query
  -> token auth check (~/.config/feedme/credentials.json)
  -> discovery (get_addresses -> search_menu, with a confirm-before-cart prompt)
  -> cart & coupon engine (flush_food_cart -> update_food_cart -> apply_food_coupon)
  -> zero-phone payment execution (place_food_order via Swiggy Money / COD, with a confirm prompt)
  -> live status polling (track_food_order)
```

Food server tools used (14 total, all live-tested except `place_food_order`
and `report_error`): `get_addresses`, `search_restaurants`,
`get_restaurant_menu`, `search_menu`, `update_food_cart`, `get_food_cart`,
`flush_food_cart`, `fetch_food_coupons`, `apply_food_coupon`,
`place_food_order`, `get_food_orders`, `track_food_order`,
`get_payment_options`, `report_error`.

One tool outside that list, `get_food_order_details`, was found to be real
too (referenced by `get_food_orders`' own output) — it's wired up in
`tracking.get_order_details_text()`, but unlike everything else it returns
no structured data at all, only human-readable text.

## Known gaps

- **`place_food_order`** — never called live. The CLI's confirmation prompts
  are the only safety gate before it fires for real.
- **Active-order tracking shape** — `track_food_order` has only been tested
  against already-delivered orders; the terminal-status phrasing in
  `tracking.py` is a best-effort guess pending a real in-flight order.
- **Instamart (`/im`) and Dineout (`/dineout`)** — untouched.
- **`report_error`** — untested; unclear side effects, not worth probing
  speculatively.

## Dev commands

```bash
pytest
ruff check .
mypy src/
```

Note: editable installs (`pip install -e .`) have been unreliable in some
dev environments for reasons never fully root-caused (see git history for
the investigation). If `import feedme`/`import models` fails after
install, try `pip install --force-reinstall --no-deps .` (a regular,
non-editable install) instead.
