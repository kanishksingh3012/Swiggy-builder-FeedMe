"""Typer entrypoint: query -> discover -> cart/coupon -> zero-phone checkout -> track."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import typer
from rich.console import Console
from rich.table import Table

from feedme import auth, cart, checkout, search, tracking
from feedme.mcp_client import MCPClient
from models import Address, MenuItem, PaymentOption

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


async def _ensure_authenticated() -> None:
    creds = auth.load_credentials()
    if creds is None or auth.is_token_expired(creds):
        await auth.login()


def _print_items_table(items: list[MenuItem]) -> None:
    table = Table(title="Results — pick one")
    table.add_column("#")
    table.add_column("Item")
    table.add_column("Restaurant")
    table.add_column("Rating")
    table.add_column("Price")
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
    fastest: bool = False,
) -> MenuItem | None:
    """Show the results table with index numbers and require an
    explicit pick before anything touches the cart. Returns None if the
    user cancels (empty input or 'q') — nothing gets ordered on your
    behalf. The 'Rating' column shows the restaurant's overall rating
    (MenuItem.restaurant_rating, filled in by search.discover() from
    search_restaurants) rather than the per-dish rating search_menu
    itself returns (MenuItem.rating — confirmed real and genuinely
    different per dish, just not what's displayed here). search_menu is
    paginated (10/page, confirmed live) — 'm' fetches and appends the
    next page rather than silently hiding the rest of the results."""
    while True:
        _print_items_table(items)
        prompt = f"Pick an item [1-{len(items)}]"
        if has_more:
            prompt += ", 'm' for more results"
        prompt += " (or 'q' to cancel)"
        raw = typer.prompt(prompt, default="q")
        choice_raw = raw.strip().lower()

        if choice_raw in ("q", ""):
            return None
        if choice_raw == "m":
            if not has_more:
                console.print("[yellow]No more results.[/]")
                continue
            new_items, has_more = await fetch_more()
            items = search.filter_items(items + new_items, fastest=fastest)
            continue

        try:
            choice = int(raw)
        except ValueError:
            console.print("[red]Not a number — cancelling.[/]")
            return None
        if not (1 <= choice <= len(items)):
            console.print(f"[red]{choice} is out of range — cancelling.[/]")
            return None
        return items[choice - 1]


async def _select_more_items(
    client: MCPClient, address_id: str, restaurant_id: str, restaurant_name: str
) -> list[MenuItem]:
    """After the first dish is picked, loop offering more dishes from
    that SAME restaurant — Swiggy carts can't mix restaurants
    (cart.add_items() enforces this), so "add another dish" only makes
    sense scoped to one restaurant's full menu, not the original search
    results (which span many restaurants). Browses via
    search.get_restaurant_menu() rather than re-filtering the original
    search, since you'd often want something the original query didn't
    match (e.g. searched "shawarma", also want a drink from the same
    place). Returns the extra items picked (possibly empty) — the
    caller combines these with the first pick into one add_items() call
    so quantities/coupons are computed against the whole order, not
    added piecemeal."""
    menu = await search.get_restaurant_menu(client, restaurant_id, address_id)
    if not menu:
        return []

    picked: list[MenuItem] = []
    picked_ids: set[str] = set()
    while True:
        table = Table(title=f"{restaurant_name} — add another dish?")
        table.add_column("#")
        table.add_column("Item")
        table.add_column("Price")
        table.add_column("Veg")
        for idx, item in enumerate(menu, start=1):
            marker = " (added)" if item.item_id in picked_ids else ""
            table.add_row(
                str(idx),
                (item.name or item.item_id) + marker,
                str(item.price) if item.price is not None else "-",
                "Veg" if item.veg else ("Non-veg" if item.veg is False else "-"),
            )
        console.print(table)

        raw = typer.prompt(f"Add a dish [1-{len(menu)}], or 'done' to continue", default="done")
        choice_raw = raw.strip().lower()
        if choice_raw in ("done", "q", ""):
            return picked

        try:
            choice = int(raw)
        except ValueError:
            console.print("[red]Not a number — try again.[/]")
            continue
        if not (1 <= choice <= len(menu)):
            console.print(f"[red]{choice} is out of range — try again.[/]")
            continue

        item = menu[choice - 1]
        if item.item_id in picked_ids:
            console.print(f"[yellow]{item.name or item.item_id} is already added.[/]")
            continue
        picked.append(item)
        picked_ids.add(item.item_id)
        console.print(f"[green]Added {item.name or item.item_id}.[/]")


def _select_payment_option(options: list[PaymentOption]) -> PaymentOption | None:
    """Same confirm-before-acting principle as _select_item, applied to
    payment method. If there's only one usable option (COD, the common
    case — no Swiggy Money on the test account), there's no actual
    decision to make, so it's used without prompting rather than adding
    a confirmation with nothing to confirm. A real choice between
    options gets the same explicit-pick treatment as menu items.

    The option set here is checkout.get_available_payment_options() —
    zero-phone options (COD/Swiggy Money) plus QR, never full mobile-app
    UPI handoffs. QR is a deliberate, explicit exception to "zero-phone"
    (see checkout.py's module docstring), so it's always labeled as such
    here rather than blended in indistinguishably."""
    if len(options) == 1:
        return options[0]

    table = Table(title="Payment options — pick one")
    table.add_column("#")
    table.add_column("Method")
    table.add_column("Type")
    for idx, option in enumerate(options, start=1):
        kind = "Mobile (payment confirmation only)" if checkout._is_qr(option) else "Zero-phone"
        table.add_row(str(idx), option.display_name or option.method_id, kind)
    console.print(table)

    raw = typer.prompt(f"Pick a payment method [1-{len(options)}] (or 'q' to cancel)", default="q")
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
    table = Table(title="Delivery address — confirm or pick")
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

    raw = typer.prompt(f"Deliver to [1-{len(top)}] (or 'q' to cancel)", default="1")
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
            nonlocal next_offset
            more_items, more_has_more, more_next_offset = await search.discover(
                client,
                query,
                address_id=address_id,
                offset=next_offset or 0,
                max_price=max_price,
                fastest=False,  # sorting is re-applied over the combined list below, not per-page
            )
            next_offset = more_next_offset
            return more_items, more_has_more

        chosen = await _select_item(items, has_more, fetch_more, fastest=fastest)
        if chosen is None:
            console.print("[yellow]Cancelled — nothing added to cart.[/]")
            return

        order_items = [chosen]
        if chosen.restaurant_id is not None:
            restaurant_name = chosen.restaurant_name or "this restaurant"
            order_items += await _select_more_items(
                client, address_id, chosen.restaurant_id, restaurant_name
            )
        if len(order_items) > 1:
            console.print(f"[cyan]{len(order_items)} items in this order.[/]")

        await cart.flush_cart(client, address_id)
        current_cart = await cart.add_items(client, address_id, order_items)
        if chosen.restaurant_id is not None:
            current_cart = await cart.apply_best_coupon(client, current_cart, chosen.restaurant_id)

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
        await tracking.track_order(client, order.order_id)


@app.command()
def main(
    query: str = typer.Argument(..., help="e.g. 'chicken bowl'"),
    max_price: float | None = typer.Option(None, "--max-price", help="Maximum item price"),
    fastest: bool = typer.Option(False, "--fastest", help="Sort by ETA ascending"),
) -> None:
    asyncio.run(run_pipeline(query, max_price, fastest))


if __name__ == "__main__":
    app()
