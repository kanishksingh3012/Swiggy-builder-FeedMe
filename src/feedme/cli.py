"""Typer entrypoint: query -> discover -> cart/coupon -> zero-phone checkout -> track."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from feedme import auth, cart, checkout, search, tracking
from feedme.mcp_client import MCPClient

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


async def run_pipeline(query: str, max_price: float | None, fastest: bool) -> None:
    await _ensure_authenticated()

    async with MCPClient("food") as client:
        # Carts/orders are addressed by addressId, not a separate cart_id
        # (confirmed live — see cart.py). Pick the first saved address as
        # the delivery address, same pragmatic "take the first result"
        # approach used below for menu items.
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

        table = Table(title=f"Results for '{query}'")
        table.add_column("Item")
        table.add_column("Price")
        table.add_column("ETA (min)")
        for item in items:
            table.add_row(
                item.name or item.item_id,
                str(item.price) if item.price is not None else "-",
                str(item.eta_minutes) if item.eta_minutes is not None else "-",
            )
        console.print(table)

        chosen = items[0]
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

        order = await checkout.checkout(client, current_cart, payment_options[0])
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
