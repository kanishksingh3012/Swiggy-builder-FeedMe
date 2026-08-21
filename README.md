# feedme

Low-friction food ordering from the terminal, via Swiggy's MCP servers.
Search, cart, coupon-optimize, and check out without a full mobile-app UPI
handoff — that's the non-negotiable part. QR is a deliberate, narrow
exception: it's offered as an explicit opt-in alongside COD/Swiggy Money
(never silently preferred), because a one-time scan-and-pay is a much
smaller ask than opening an app and authorizing inside it. See
`checkout.py`'s module docstring for the exact policy and why it changed
from the original "no QR, ever" framing.

> **Verification status (2026-08-21):** the wire protocol, argument casing,
> and most tool response shapes below are now **live-verified** against the
> real server — not the doc-derived guesses this started as. See each
> module's docstring for exactly what was confirmed and how (`mcp_client.py`
> for the protocol, `search.py`/`cart.py`/`checkout.py`/`tracking.py` for
> per-tool findings). Models stay permissive (`extra="allow"`) regardless,
> since the server itself mixes camelCase and snake_case field names in the
> same object.
>
> **`place_food_order` is now confirmed live** for the QR path — a real
> order was placed with a genuine, scannable UPI QR that correctly opened
> payment apps when scanned. Two real failed attempts got it there (wrong
> argument name, then wrong argument value — see `checkout.py`'s
> docstrings). **Still unverified:** the COD path specifically (fixed the
> same way, not yet independently confirmed), the shape of an *active*
> order's tracking data (no in-flight order existed to test against until
> now), and `report_error`.

## Install

```bash
pip install -e ".[dev]"
```

## Quickstart

```bash
python -m feedme.auth login
feedme "chicken bowl" --max-price 400 --fastest
```

The CLI will show matching results (paginated — `m` loads more) and **wait
for you to pick one** — it never adds anything to your cart on its own. If
more than one usable payment method is available (COD, Swiggy Money, or
QR — never a full UPI app handoff), it asks you to pick; if there's only
one, it's used without an extra prompt since there's no real choice to
confirm.

## Architecture

```
CLI flags/query
  -> token auth check (~/.config/feedme/credentials.json)
  -> discovery (get_addresses -> search_menu, with a confirm-before-cart prompt)
  -> cart & coupon engine (flush_food_cart -> update_food_cart -> apply_food_coupon)
  -> payment execution (place_food_order via COD / Swiggy Money / QR, with a confirm prompt)
  -> live status polling (track_food_order)
```

Food server tools used (14 total, all live-tested including
`place_food_order` now, except `report_error`): `get_addresses`,
`search_restaurants`, `get_restaurant_menu`, `search_menu`,
`update_food_cart`, `get_food_cart`, `flush_food_cart`,
`fetch_food_coupons`, `apply_food_coupon`, `place_food_order`,
`get_food_orders`, `track_food_order`, `get_payment_options`,
`report_error`.

One tool outside that list, `get_food_order_details`, was found to be real
too (referenced by `get_food_orders`' own output) — it's wired up in
`tracking.get_order_details_text()`, but unlike everything else it returns
no structured data at all, only human-readable text.

## Known gaps

- **`place_food_order` (COD path)** — the QR path is confirmed live (see
  above); COD's `paymentMethod="Cash"` is fixed the same way but hasn't
  been independently confirmed by an actual completed COD order.
- **QR payload field name** — confirmed live that one of
  `checkout.find_qr_payload()`'s candidate keys does match a real
  response (it found a genuine UPI string that rendered as a working QR),
  but the exact key wasn't logged at the time, so the list stays a
  superset rather than narrowing to one confirmed name.
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
