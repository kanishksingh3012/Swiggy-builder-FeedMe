"""Typer entrypoint: query -> discover -> cart/coupon -> zero-phone checkout -> track."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from feedme import auth, cart, checkout, search, tracking
from feedme.mcp_client import MCPClient
from models import MenuItem, PaymentOption

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


def _select_item(items: list[MenuItem]) -> MenuItem | None:
    """Show the results table with index numbers and require an
    explicit pick before anything touches the cart. Returns None if the
    user cancels (empty input or 'q') — nothing gets ordered on your
    behalf."""
    table = Table(title="Results — pick one")
    table.add_column("#")
    table.add_column("Item")
    table.add_column("Price")
    table.add_column("ETA (min)")
    for idx, item in enumerate(items, start=1):
        table.add_row(
            str(idx),
            item.name or item.item_id,
            str(item.price) if item.price is not None else "-",
            str(item.eta_minutes) if item.eta_minutes is not None else "-",
        )
    console.print(table)

    raw = typer.prompt(f"Pick an item [1-{len(items)}] (or 'q' to cancel)", default="q")
    if raw.strip().lower() in ("q", ""):
        return None
    try:
        choice = int(raw)
    except ValueError:
        console.print("[red]Not a number — cancelling.[/]")
        return None
    if not (1 <= choice <= len(items)):
        console.print(f"[red]{choice} is out of range — cancelling.[/]")
        return None
    return items[choice - 1]


def _select_payment_option(options: list[PaymentOption]) -> PaymentOption | None:
    """Same confirm-before-acting principle as _select_item, applied to
    payment method. If there's only one zero-phone option (the only case
    seen live so far — COD, no Swiggy Money on the test account), there's
    no actual decision to make, so it's used without prompting rather
    than adding a confirmation with nothing to confirm. A real choice
    between options (e.g. once Swiggy Money is also available) still
    gets the same explicit-pick treatment as menu items."""
    if len(options) == 1:
        return options[0]

    table = Table(title="Zero-phone payment options — pick one")
    table.add_column("#")
    table.add_column("Method")
    for idx, option in enumerate(options, start=1):
        table.add_row(str(idx), option.display_name or option.method_id)
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


async def run_pipeline(query: str, max_price: float | None, fastest: bool) -> None:
    await _ensure_authenticated()

    async with MCPClient("food") as client:
        # Carts/orders are addressed by addressId, not a separate cart_id
        # (confirmed live — see cart.py). The delivery address is still
        # autopicked (the account's first saved address) — that part was
        # explicitly fine to keep simple. The menu item is not: nothing
        # goes into the cart without an explicit pick below.
        addresses = await search.get_addresses(client)
        if not addresses:
            console.print("[yellow]No saved addresses on this account.[/]")
            return
        address_id = addresses[0].id

        items = await search.discover(
            client, query, address_id=address_id, max_price=max_price, fastest=fastest
        )
        if not items:
            console.print("[yellow]No menu items found for that query.[/]")
            return

        chosen = _select_item(items)
        if chosen is None:
            console.print("[yellow]Cancelled — nothing added to cart.[/]")
            return

        await cart.flush_cart(client, address_id)
        await cart.add_items(client, address_id, [chosen])
        current_cart = await cart.get_cart(client, address_id)
        if chosen.restaurant_id is not None:
            current_cart = await cart.apply_best_coupon(client, current_cart, chosen.restaurant_id)

        try:
            payment_options = await checkout.get_zero_phone_payment_options(client)
        except checkout.ZeroPhonePaymentUnavailable as exc:
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
