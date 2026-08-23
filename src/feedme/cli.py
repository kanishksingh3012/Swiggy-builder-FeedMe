"""Typer entrypoint: query -> discover -> cart/coupon -> zero-phone checkout -> track."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import typer
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from feedme import auth, cart, checkout, search, tracking
from feedme.mcp_client import MCPClient
from models import AddonGroup, Address, Cart, MenuItem, PaymentOption

# `main` is registered via @app.command() (not @app.callback()): a Typer
# app whose only entrypoint is a single @app.command() collapses to a
# plain Click Command instead of a Group. That matters — Groups don't
# allow options after a leading positional argument, which would break
# the documented invocation `feedme "chicken bowl" --max-price 400
# --fastest`. Auth is a separate entrypoint per CLAUDE.md
# (`python -m feedme.auth login`) precisely so this app doesn't need a
# `login` subcommand that would force it into Group mode.
app = typer.Typer(name="feedme", help="Zero-phone food ordering from the terminal.")

console = Console()

# Swiggy's actual brand orange — used for every accent/highlight instead
# of Rich's default cyan, so the CLI reads as "Swiggy" rather than
# generic terminal-tool blue.
BRAND = "#fc8019"


def _table(title: str) -> Table:
    """Every table in this CLI uses the same branded styling — one
    place to keep that consistent rather than repeating it per call
    site."""
    return Table(title=title, header_style=f"bold {BRAND}", border_style=BRAND)


def _print_banner() -> None:
    console.print(
        Panel(
            Align.center(f"[bold {BRAND}]F E E D M E[/]"),
            border_style=BRAND,
            padding=(0, 2),
        )
    )


# How many search_menu pages (10 items each) 'm' fetches from the server
# per press. See fetch_more()'s docstring in run_pipeline for why this
# isn't 1.
PAGES_PER_LOAD_MORE = 5

# How many items are shown on screen at once. Server-side fetches (above)
# stay bigger than this — the whole batch gets relevance-ranked together —
# but only this many are ever displayed at a time, revealing more from the
# already-fetched pool before going back to the server. Too many options
# on screen at once was flagged directly as a real usability problem, not
# just a display preference.
RESULTS_PAGE_SIZE = 5


async def _ensure_authenticated() -> None:
    creds = auth.load_credentials()
    if creds is None or auth.is_token_expired(creds):
        await auth.login()


def _print_actions(*actions: tuple[str, str]) -> None:
    """A short, scannable legend of the available inputs — one action
    per line, key left-aligned — shown right before a prompt. Direct
    feedback: cramming every option into one run-on prompt sentence
    ("add a dish [1-5], 'm' for more dishes, 'r<#>' to remove..., 'done'
    to continue", all in one breath) was confusing, even though the
    underlying flow was fine. This is a presentation fix, not a logic
    one — every picker below now follows the same pattern: table (data)
    -> action legend (choices) -> a short, generic prompt."""
    console.print()
    for key, desc in actions:
        console.print(f"  [bold {BRAND}]{key:<10}[/][dim]{desc}[/]")
    console.print()


def _print_items_table(items: list[MenuItem]) -> None:
    # "Base price" (not "Price"): Swiggy has no per-item tax-inclusive
    # figure anywhere — delivery charge and taxes are computed against
    # the whole cart once items are actually added, not per dish (see
    # _print_cart_summary for the real final numbers). Labeling this
    # "Price" invited exactly the "is this the final price?" question
    # that came up — it isn't, and can't be, until there's a cart.
    table = _table("Results — pick one")
    table.add_column("#")
    table.add_column("Item")
    table.add_column("Restaurant")
    table.add_column("Rating")
    table.add_column("Base price")
    table.add_column("ETA (min)")
    for idx, item in enumerate(items, start=1):
        table.add_row(
            str(idx),
            item.name or item.item_id,
            item.restaurant_name or "-",
            str(item.restaurant_rating) if item.restaurant_rating is not None else "-",
            str(item.price) if item.price is not None else "-",
            str(item.eta_minutes) if item.eta_minutes is not None else "-",
        )
    console.print(table)


async def _select_item(
    items: list[MenuItem],
    has_more: bool,
    fetch_more: Callable[[], Awaitable[tuple[list[MenuItem], bool]]],
    *,
    query: str | None = None,
    fastest: bool = False,
) -> MenuItem | None:
    """Show up to RESULTS_PAGE_SIZE items at a time and require an
    explicit pick before anything touches the cart. Returns None if the
    user cancels (empty input or 'q') — nothing gets ordered on your
    behalf. Showing everything fetched at once (previously up to ~60
    after a few 'm' presses) was flagged directly as overwhelming and
    counterproductive — too many options defeats the point of a quick
    terminal order. 'm' first reveals more from what's already been
    fetched (no server call) before reaching further via fetch_more(),
    which pulls a bigger server-side batch (PAGES_PER_LOAD_MORE pages)
    all at once — a real match can sit far enough down the server's own
    ranking that fetching one page at a time would need many round
    trips (confirmed live: rank ~47 among 60 fetched for a 2-word
    query). The 'Rating' column shows the restaurant's overall rating
    (MenuItem.restaurant_rating, filled in by search.discover() from
    search_restaurants) rather than the per-dish rating search_menu
    itself returns (MenuItem.rating — confirmed real and genuinely
    different per dish, just not what's displayed here). Merged results
    are re-ranked via search.filter_items(query=...) so an exact name
    match pulled in by 'm' still surfaces near the top rather than
    wherever Swiggy's own ranking placed it."""
    shown = min(RESULTS_PAGE_SIZE, len(items))
    while True:
        window = items[:shown]
        _print_items_table(window)
        more_available = shown < len(items) or has_more
        actions = [(f"1-{len(window)}", "pick this item")]
        if more_available:
            actions.append(("m", "show more results"))
        actions.append(("q", "cancel"))
        _print_actions(*actions)
        raw = typer.prompt("Your choice", default="q")
        choice_raw = raw.strip().lower()

        if choice_raw in ("q", ""):
            return None
        if choice_raw == "m":
            if shown < len(items):
                shown = min(shown + RESULTS_PAGE_SIZE, len(items))
                continue
            if not has_more:
                console.print("[yellow]No more results.[/]")
                continue
            new_items, has_more = await fetch_more()
            items = search.filter_items(items + new_items, query=query, fastest=fastest)
            shown = min(shown + RESULTS_PAGE_SIZE, len(items))
            continue

        try:
            choice = int(raw)
        except ValueError:
            console.print("[red]Not a number — cancelling.[/]")
            return None
        if not (1 <= choice <= len(window)):
            console.print(f"[red]{choice} is out of range — cancelling.[/]")
            return None
        return window[choice - 1]


def _print_order_so_far(order: list[MenuItem]) -> None:
    table = _table("Your order so far")
    table.add_column("#")
    table.add_column("Item")
    table.add_column("Base price")
    for idx, item in enumerate(order, start=1):
        table.add_row(str(idx), item.name or item.item_id, str(item.price or "-"))
    console.print(table)


def _pick_from_menu(
    menu: list[MenuItem], restaurant_name: str, order: list[MenuItem]
) -> MenuItem | None:
    """Browse-and-pick sub-flow, only ever entered when the user
    explicitly asks to add a dish (see _build_order_items) — the menu
    table used to be shown unconditionally on every loop, which was
    flagged directly as an unwanted extra step for the common case of
    just wanting one dish. Returns None if the user backs out without
    picking anything."""
    order_ids = {i.item_id for i in order}
    shown = min(RESULTS_PAGE_SIZE, len(menu))
    while True:
        window = menu[:shown]
        table = _table(f"{restaurant_name} menu")
        table.add_column("#")
        table.add_column("Item")
        table.add_column("Base price")
        table.add_column("Veg")
        for idx, item in enumerate(window, start=1):
            marker = " (in order)" if item.item_id in order_ids else ""
            table.add_row(
                str(idx),
                (item.name or item.item_id) + marker,
                str(item.price) if item.price is not None else "-",
                "Veg" if item.veg else ("Non-veg" if item.veg is False else "-"),
            )
        console.print(table)

        actions = [(f"1-{len(window)}", "add this dish")]
        if shown < len(menu):
            actions.append(("m", "show more dishes"))
        actions.append(("q", "back"))
        _print_actions(*actions)
        raw = typer.prompt("Your choice", default="q")
        choice_raw = raw.strip().lower()

        if choice_raw in ("q", ""):
            return None
        if choice_raw == "m":
            if shown < len(menu):
                shown = min(shown + RESULTS_PAGE_SIZE, len(menu))
            else:
                console.print("[yellow]No more dishes.[/]")
            continue

        try:
            choice = int(raw)
        except ValueError:
            console.print("[red]Not a number — try again.[/]")
            continue
        if not (1 <= choice <= len(window)):
            console.print(f"[red]{choice} is out of range — try again.[/]")
            continue
        return window[choice - 1]


async def _build_order_items(
    client: MCPClient, address_id: str, first_item: MenuItem
) -> list[MenuItem]:
    """Interactive cart builder, seeded with the already-picked first
    dish. Swiggy carts can't mix restaurants (cart.add_items()
    enforces this), so browsing more options is scoped to that
    restaurant's full menu, not the original search results (which span
    many restaurants) — via search.get_restaurant_menu() rather than
    re-filtering the original search, since you'd often want something
    the original query didn't match (e.g. searched "shawarma", also
    want a drink from the same place).

    The default screen here is just "your order so far" plus add/
    remove/done — the restaurant's full menu (fetched lazily, only on
    'a') isn't shown unless actively requested, since always showing it
    was flagged directly as an unwanted extra step for the common case
    of wanting just one dish. 'r<#>' removes an item from the order
    (including the first pick) before anything is ever sent to the real
    cart — there was no way to undo a pick at all before this. Returns
    the final list, possibly empty if everything got removed; the
    caller treats an empty result as a full cancellation, same as
    _select_item's 'q'."""
    order: list[MenuItem] = [first_item]
    restaurant_id = first_item.restaurant_id
    restaurant_name = first_item.restaurant_name or "this restaurant"
    menu: list[MenuItem] | None = None

    while True:
        _print_order_so_far(order)
        actions = [("a", "add another dish")]
        if order:
            actions.append(("r<#>", "remove an item, e.g. 'r2'"))
        actions.append(("done", "continue to checkout"))
        _print_actions(*actions)
        raw = typer.prompt("Your choice", default="done")
        choice_raw = raw.strip().lower()

        if choice_raw in ("done", "q", ""):
            return order
        if choice_raw.startswith("r") and choice_raw[1:].isdigit():
            idx = int(choice_raw[1:])
            if not (1 <= idx <= len(order)):
                console.print(f"[red]{idx} is out of range for your order.[/]")
                continue
            removed = order.pop(idx - 1)
            console.print(f"[yellow]Removed {removed.name or removed.item_id}.[/]")
            continue
        if choice_raw != "a":
            console.print("[red]Unrecognized choice — try again.[/]")
            continue

        if menu is None:
            menu = []
            if restaurant_id:
                menu = await search.get_restaurant_menu(client, restaurant_id, address_id)
        if not menu:
            console.print("[yellow]No other dishes available from this restaurant.[/]")
            continue

        picked = _pick_from_menu(menu, restaurant_name, order)
        if picked is None:
            continue
        if picked.item_id in {i.item_id for i in order}:
            console.print(f"[yellow]{picked.name or picked.item_id} is already in your order.[/]")
            continue
        order.append(picked)
        console.print(f"[green]Added {picked.name or picked.item_id}.[/]")


def _print_cart_summary(cart_obj: Cart) -> None:
    """The real, final numbers — item total, delivery, taxes, and what
    you'll actually pay — shown once before payment selection. Sourced
    from Cart.data.pricing (confirmed live 2026-08-21: item_total,
    delivery_charge, taxes_and_charges, to_pay) since there's no
    per-item tax-inclusive figure anywhere in Swiggy's API — tax and
    delivery are computed against the whole cart, not per dish, so this
    is the earliest point a "final price" genuinely exists at all."""
    pricing = (cart_obj.data or {}).get("pricing")
    if not pricing:
        return
    offers = (cart_obj.data or {}).get("offers") or {}

    table = _table("Order summary")
    table.add_column("")
    table.add_column("", justify="right")
    table.add_row("Item total", f"₹{pricing.get('item_total', '-')}")
    table.add_row("Delivery charge", f"₹{pricing.get('delivery_charge', '-')}")
    if offers.get("coupon_discount"):
        table.add_row("Coupon discount", f"-₹{offers['coupon_discount']}")
    table.add_row("Taxes & charges", f"₹{pricing.get('taxes_and_charges', '-')}")
    table.add_row("[bold]To pay[/]", f"[bold]₹{pricing.get('to_pay', '-')}[/]")
    console.print(table)


def _select_payment_option(options: list[PaymentOption]) -> PaymentOption | None:
    """Same confirm-before-acting principle as _select_item, applied to
    payment method. If the sole option is zero-phone (COD/Swiggy Money —
    the common case), it's used without prompting since there's no real
    decision to make and no consequence to auto-picking it. QR is
    different and always requires an explicit yes, even as the only
    option: unlike COD, generating one is a real action (it creates a
    genuine UPI payment request) — confirmed live 2026-08-21 when a test
    script with too few piped inputs sailed through an unprompted
    single-QR-option case and generated a real one with nobody
    confirming anything. COD auto-picking is still safe since nothing
    is requested/charged by the pick itself.

    The option set here is checkout.get_available_payment_options() —
    zero-phone options (COD/Swiggy Money) plus QR, never full mobile-app
    UPI handoffs. QR is a deliberate, explicit exception to "zero-phone"
    (see checkout.py's module docstring), so it's always labeled as such
    here rather than blended in indistinguishably."""
    if len(options) == 1 and not checkout._is_qr(options[0]):
        return options[0]

    if len(options) == 1:
        only = options[0]
        console.print(
            f"[{BRAND}]Only payment option available: {only.display_name or only.method_id} "
            "(Mobile, payment confirmation only).[/]"
        )
        _print_actions(("y", "generate the QR and proceed"), ("n", "cancel (default)"))
        raw = typer.prompt("Your choice", default="n")
        return only if raw.strip().lower() in ("y", "yes") else None

    table = _table("Payment options — pick one")
    table.add_column("#")
    table.add_column("Method")
    table.add_column("Type")
    for idx, option in enumerate(options, start=1):
        kind = "Mobile (payment confirmation only)" if checkout._is_qr(option) else "Zero-phone"
        table.add_row(str(idx), option.display_name or option.method_id, kind)
    console.print(table)

    _print_actions((f"1-{len(options)}", "pick a payment method"), ("q", "cancel"))
    raw = typer.prompt("Your choice", default="q")
    if raw.strip().lower() in ("q", ""):
        return None
    try:
        choice = int(raw)
    except ValueError:
        console.print("[red]Not a number — cancelling.[/]")
        return None
    if not (1 <= choice <= len(options)):
        console.print(f"[red]{choice} is out of range — cancelling.[/]")
        return None
    return options[choice - 1]


def _prompt_addon_choice(group: AddonGroup) -> None:
    """Shows one mandatory group's choices with prices and lets the
    user pick, purely for their own reference before finishing the
    order in the Swiggy app — feedme can't submit this selection back
    to Swiggy itself (see cart.MandatoryAddonRequired's docstring: every
    request shape tried was rejected identically, live-confirmed
    2026-08-23 to be a gap in Swiggy's own MCP tools, not a wrong
    parameter name). Picking here never claims to add anything to the
    cart — it only echoes the choice back so it's not a "confirm" that
    silently does nothing."""
    console.print(f"  [bold {BRAND}]{group.group_name or group.group_id}[/] [dim]— pick one[/]")
    for idx, choice in enumerate(group.choices, start=1):
        price = f"+₹{choice.price:.0f}" if choice.price else "+₹0"
        console.print(f"    [dim]{idx}.[/] {choice.name or choice.id}  [dim]{price}[/]")
    raw = typer.prompt(
        f"  Choose {(group.group_name or 'an option').lower()} [1-{len(group.choices)}]",
        default="1",
    )
    try:
        idx = int(raw)
    except ValueError:
        return
    if 1 <= idx <= len(group.choices):
        picked = group.choices[idx - 1]
        console.print(f"  [dim]{picked.name or picked.id} selected.[/]")


def _print_mandatory_addon_notice(exc: cart.MandatoryAddonRequired) -> None:
    console.print(f"[{BRAND}]{exc.item_name}[/] needs a choice before it can be added:\n")
    for group in exc.groups:
        _prompt_addon_choice(group)
    console.print()
    console.print(
        "[yellow]Can't add this to your cart yet[/] — Swiggy's ordering tools don't "
        "currently accept this kind of choice, even after picking one here. This is a "
        "gap on Swiggy's side, not feedme.\n"
        "[dim]Order this dish through the Swiggy app instead, or try a dish without a "
        "required choice.[/]"
    )


def _address_priority(address: Address) -> int:
    """Confirmed live (2026-08-21) that addressCategory=="Home" is not
    unique — an account had 3 addresses categorized "Home" — so category
    alone can't safely auto-pick. The specific address tagged exactly
    "Home" (not just categorized Home) is the actual signal; everything
    else falls back to Home-category, then the rest, in original order
    within each group (sort is stable)."""
    tag = (address.address_tag or "").strip().lower()
    if tag == "home":
        return 0
    if (address.address_category or "").strip().lower() == "home":
        return 1
    return 2


def _select_address(addresses: list[Address]) -> Address | None:
    """Confirm-before-acting, same principle as _select_item — an
    address mistake means delivering to the wrong city, a real live
    failure mode caught in testing (default was addresses[0], which
    turned out to be a stale saved address in a different city entirely
    from where the account's actual "Home" address is). Shows only the
    top 3 (sorted by _address_priority) rather than the full list —
    the sort already does the real work of surfacing the likely match
    first; this is just a final human check, not another full picker."""
    top = sorted(addresses, key=_address_priority)[:3]
    table = _table("Delivery address — confirm or pick")
    table.add_column("#")
    table.add_column("Tag")
    table.add_column("Category")
    table.add_column("Address")
    for idx, addr in enumerate(top, start=1):
        table.add_row(
            str(idx),
            addr.address_tag or "-",
            addr.address_category or "-",
            (addr.address_line or "")[:60],
        )
    console.print(table)

    _print_actions((f"1-{len(top)}", "deliver here"), ("q", "cancel"))
    raw = typer.prompt("Your choice", default="1")
    if raw.strip().lower() in ("q", ""):
        return None
    try:
        choice = int(raw)
    except ValueError:
        console.print("[red]Not a number — cancelling.[/]")
        return None
    if not (1 <= choice <= len(top)):
        console.print(f"[red]{choice} is out of range — cancelling.[/]")
        return None
    return top[choice - 1]


async def run_pipeline(query: str, max_price: float | None, fastest: bool) -> None:
    _print_banner()
    await _ensure_authenticated()

    async with MCPClient("food") as client:
        # Carts/orders are addressed by addressId, not a separate cart_id
        # (confirmed live — see cart.py). Address selection used to be
        # addresses[0] with no confirmation — a real live check showed
        # that's not safe (the first result was a stale address in a
        # different city than the account's actual "Home"). Now sorted
        # to put the likely match first and confirmed explicitly, same
        # principle as the item/payment pickers.
        addresses = await search.get_addresses(client)
        if not addresses:
            console.print("[yellow]No saved addresses on this account.[/]")
            return
        address = _select_address(addresses)
        if address is None:
            console.print("[yellow]Cancelled — no delivery address confirmed.[/]")
            return
        address_id = address.id

        items, has_more, next_offset = await search.discover(
            client, query, address_id=address_id, max_price=max_price, fastest=fastest
        )
        if not items:
            console.print("[yellow]No menu items found for that query.[/]")
            return

        async def fetch_more() -> tuple[list[MenuItem], bool]:
            # Fetches several pages per 'm' press, not just one. A real
            # match can sit ~50 results deep even after the relevance
            # boost (confirmed live: 56 of 60 results for a 2-word query
            # all matched, so a real item stayed buried around rank 47) —
            # one page (10 items) per press would need ~5 presses to even
            # fetch it. This trades a few extra API calls for far less
            # manual paging.
            nonlocal next_offset
            batch: list[MenuItem] = []
            more_has_more = True
            for _ in range(PAGES_PER_LOAD_MORE):
                more_items, more_has_more, more_next_offset = await search.discover(
                    client,
                    query,
                    address_id=address_id,
                    offset=next_offset or 0,
                    max_price=max_price,
                    fastest=False,  # sorting is re-applied over the combined list, not per-page
                )
                batch += more_items
                next_offset = more_next_offset
                if not more_has_more:
                    break
            return batch, more_has_more

        chosen = await _select_item(items, has_more, fetch_more, query=query, fastest=fastest)
        if chosen is None:
            console.print("[yellow]Cancelled — nothing added to cart.[/]")
            return

        order_items = await _build_order_items(client, address_id, chosen)
        if not order_items:
            console.print("[yellow]Cancelled — nothing added to cart.[/]")
            return
        if len(order_items) > 1:
            console.print(f"[{BRAND}]{len(order_items)} items in this order.[/]")

        await cart.flush_cart(client, address_id)
        try:
            current_cart = await cart.add_items(client, address_id, order_items)
        except cart.MandatoryAddonRequired as exc:
            _print_mandatory_addon_notice(exc)
            return
        except cart.CartUpdateFailed as exc:
            console.print(f"[red]Couldn't add to cart: {exc}[/]")
            if "INVALID_ADDON" in exc.error_codes:
                console.print(
                    "[yellow]This item needs an option selected (size, spice level, etc.) "
                    "that feedme can't do yet — try a different item.[/]"
                )
            return
        if chosen.restaurant_id is not None:
            current_cart = await cart.apply_best_coupon(client, current_cart, chosen.restaurant_id)
        _print_cart_summary(current_cart)

        try:
            payment_options = await checkout.get_available_payment_options(client)
        except checkout.NoUsablePaymentOptions as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(code=1) from exc

        payment_option = _select_payment_option(payment_options)
        if payment_option is None:
            console.print("[yellow]Cancelled — order not placed.[/]")
            return

        order = await checkout.checkout(client, current_cart, payment_option)
        await tracking.track_order(client, order.order_id, address_id)


@app.command()
def main(
    query: str = typer.Argument(..., help="e.g. 'chicken bowl'"),
    max_price: float | None = typer.Option(None, "--max-price", help="Maximum item price"),
    fastest: bool = typer.Option(False, "--fastest", help="Sort by ETA ascending"),
) -> None:
    asyncio.run(run_pipeline(query, max_price, fastest))


if __name__ == "__main__":
    app()
