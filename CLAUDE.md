# CLAUDE.md — Project Context & Rules for `feedme`

You have access to Swiggy Builders Club docs - the authoritative source
for Swiggy MCP (Food, Instamart, Dineout). Always consult these before
writing Swiggy code:

- Index:      https://mcp.swiggy.com/builders/llms.txt
- Full text:  https://mcp.swiggy.com/builders/llms-full.txt
- Per-page:   append `.md` to any https://mcp.swiggy.com/builders/docs/... URL

Tool schemas live under `/docs/reference/{food,instamart,dineout}`.
Error codes live at `/docs/reference/errors`. Auth flow is at
`/docs/start/authenticate`.

Rules:
1. Before recommending a tool name, parameter, error code, rate limit,
   or auth flow, fetch the relevant doc and verify.
2. Never invent tool names or parameters. If the docs don't cover it,
   say so and ask.
3. Prefer `.md` page fetches over `llms-full.txt` when you know the
   exact area - it's cheaper on context.

Smoke test: fetch llms.txt and tell me how many tools the Food server
exposes. (Answer: 14.)

## 1. Project Overview
`feedme` is a developer CLI tool for zero-phone food and grocery ordering via Swiggy's Model Context Protocol (MCP) servers (`https://mcp.swiggy.com`).

**Core Mission:** Automate food discovery, menu parsing, coupon optimization, and 1-click checkout directly in the terminal without breaking developer focus or requiring a mobile phone.

---

## 2. Tech Stack
* **Language/Runtime:** Python 3.11+
* **CLI Framework:** `typer` (CLI commands & flags) + `rich` (terminal formatting, tables, spinners)
* **HTTP Client:** `httpx` (async JSON-RPC / REST client)
* **Data Validation:** `pydantic` v2
* **Testing & Quality:** `pytest` + `ruff` + `mypy`

---

## 3. Architecture & Core Workflow Pipeline

[ CLI Flags / Query Input ]
       │
       ▼
[ Token Auth Check (~/.config/feedme/credentials.json) ]
       │
       ▼
[ Discovery Engine (get_addresses ➔ search_menu) ]
       │
       ▼
[ Cart & Coupon Engine (flush_food_cart ➔ update_food_cart ➔ apply_food_coupon) ]
       │
       ▼
[ Zero-Phone Payment Execution (place_food_order ➔ Swiggy Money / COD) ]
       │
       ▼
[ Live Status Polling (track_food_order) ]

---

## 4. Key Rules & Constraints

### Zero-Phone Constraint
* **Bypass Mobile QR & UPI PINs:** Never trigger payment methods that force QR camera scanning or mobile app authorization.
* **Allowed Payment Methods:** Query `get_payment_options` and filter strictly for `Swiggy Money` (Prepaid Wallet) or `COD` (Cash on Delivery) for single-tap execution.

### OAuth 2.1 PKCE Lifecycle
* **Protocol:** OAuth 2.1 PKCE with SHA-256 (`S256`) code challenge.
* **Storage Path:** Store tokens securely in `~/.config/feedme/credentials.json`.
* **Token Refresh:** Swiggy access tokens expire after 5 days (`expires_in = 432000`). Intercept `401 Unauthorized` responses and trigger the re-auth server at `http://localhost:3000/callback`.

### Swiggy MCP Endpoints
* **Base Auth:** `https://mcp.swiggy.com`
* **Food Server:** `/food` (14 tools)
* **Instamart Server:** `/im` (13 tools)
* **Dineout Server:** `/dineout` (8 tools)

---

## 5. Repository Structure
feedme/
├── src/
│   ├── feedme/
│   │   ├── cli.py            # Typer entrypoint and CLI flag commands
│   │   ├── auth.py           # PKCE OAuth flow, local HTTP callback server, token refresh
│   │   ├── mcp_client.py     # Async HTTP client injecting Bearer headers to Swiggy endpoints
│   │   ├── search.py         # Menu parsing and item filtering (price, ETA, protein)
│   │   ├── cart.py           # Cart flushing, updating, and coupon maximization logic
│   │   ├── checkout.py       # Swiggy Money / COD payment execution and order lock
│   │   └── tracking.py       # Terminal progress animation polling track_food_order
│   └── models.py             # Pydantic data schemas for MCP requests & responses
├── tests/                    # Pytest suite with mocked Swiggy MCP endpoints
├── pyproject.toml            # Poetry / Hatch package setup
└── CLAUDE.md

---

## 6. Development & Workflows

# Authenticate session
python -m feedme.auth login

# Execute food search
python -m feedme.cli "chicken bowl" --max-price 400 --fastest

# Run test suite and static analysis
pytest
ruff check .
mypy src/

---

## 7. Code Style Conventions
* **Async Operations:** All API-facing network calls inside `mcp_client.py` must use `httpx.AsyncClient`.
* **Explicit Auth Handling:** Catch HTTP `401` errors explicitly and route to `feedme.auth.reauthenticate()`.
* **Terminal UI:** Render item options and checkout receipts cleanly using `rich.table.Table`.

---

## 8. MCP Docs Verification Log

### 2026-08-20 — initial doc-based verification
`mcp.swiggy.com/builders` index/reference pages returned inconsistent
tool counts on generic index fetches (15 vs. 17 tools listed across two
fetches). Targeted fetches of `llms-full.txt`, `/docs/start/authenticate.md`,
`/docs/reference/errors.md`, and `/docs/operate/rate-limits.md` were
internally consistent and corroborated this file's own stated facts
(14 Food tools, `expires_in=432000`). Auth flow (OAuth 2.1 PKCE,
`/auth/authorize` + `/auth/token`) confirmed working live the same day —
a real access token was obtained end-to-end.

### 2026-08-20/21 — live tool-by-tool verification
Doc fetches gave the right *auth* details but the wrong *tool-call*
details — actually calling each tool overturned several doc-derived
guesses:
- Wire format is JSON-RPC 2.0 over Streamable HTTP (`method: "tools/call"`,
  requires `Accept: application/json, text/event-stream` or 406s) — not
  the REST-style guess this was originally built against.
- Tool arguments are camelCase (`addressId`, not `address_id`).
- Domain errors surface as `isError: true` inside a 200 JSON-RPC result,
  not as HTTP errors or JSON-RPC `error` objects.
- There is no `cart_id` — carts/coupons/orders are all addressed by
  `addressId` (same id as `get_addresses`).
- Real response shapes for addresses, menu search, restaurant search,
  restaurant menus, cart, coupons, past orders, and payment options were
  all captured from live calls and are now what `models.py` is built
  against (see each model's docstring for specifics and the date it was
  confirmed). Two different tools return menu items with two different
  id field names (`menu_item_id` vs `id`) for what's otherwise the same
  shape — reconciled via `pydantic.AliasChoices`, not a typo.
- `get_food_order_details` is a real tool (the server's own output
  referenced it) that was missing from the original 14-tool doc-derived
  list entirely — that list undercounted by at least one.
- `apply_food_coupon` confirmed live: `couponCode` is the coupon's
  human-readable `title` (e.g. `"SPECIALS"`), not its `id` UUID. Its own
  terms state it can only be applied once per 2 hours per restaurant —
  a real, non-monetary cost, which is why it wasn't tested more than once.
- `place_food_order` remains deliberately never called live — the CLI's
  confirmation prompts (item, then payment method) are the only gate in
  front of it.

`search_restaurants` and `get_restaurant_menu` were also live-verified
(2026-08-21) — the latter nests items under `categories[].items` rather
than a flat `items` list, unlike `search_menu`.

Wire-format/shape assumptions still resting on inference rather than a
live call: an *active* order's `track_food_order` shape (only "nothing
to track" was observed, since no in-flight order existed to test
against), and `report_error`.
