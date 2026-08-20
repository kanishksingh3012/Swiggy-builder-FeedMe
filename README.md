# feedme

Zero-phone food ordering from the terminal, via Swiggy's MCP servers. Search,
cart, coupon-optimize, and check out without touching your phone — no
QR-scan or UPI-PIN mobile handoff, ever.

> **Docs status:** the Food-tool list, OAuth endpoints, error-handling
> pattern, and rate limits below were verified against targeted fetches of
> `mcp.swiggy.com/builders` docs (see `CLAUDE.md` §8 for the verification
> log). The exact MCP tool-call wire format (request/response JSON shape)
> and `payment_option` field names are still unverified — models are
> intentionally permissive (`extra="allow"`) so unexpected fields don't
> crash the client.

## Install

```bash
pip install -e ".[dev]"
```

## Quickstart

```bash
python -m feedme.auth login
feedme "chicken bowl" --max-price 400 --fastest
```

## Architecture

```
CLI flags/query
  -> token auth check (~/.config/feedme/credentials.json)
  -> discovery (get_addresses -> search_menu)
  -> cart & coupon engine (flush_food_cart -> update_food_cart -> apply_food_coupon)
  -> zero-phone payment execution (place_food_order via Swiggy Money / COD)
  -> live status polling (track_food_order)
```

Food server tools used (verified list, 14 total): `get_addresses`,
`search_restaurants`, `get_restaurant_menu`, `search_menu`,
`update_food_cart`, `get_food_cart`, `flush_food_cart`,
`fetch_food_coupons`, `apply_food_coupon`, `place_food_order`,
`get_food_orders`, `track_food_order`, `get_payment_options`,
`report_error`.

## Dev commands

```bash
pytest
ruff check .
mypy src/
```
