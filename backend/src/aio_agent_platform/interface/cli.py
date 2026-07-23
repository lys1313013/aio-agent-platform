"""CLI interface — interactive REPL for the agent."""

import asyncio
import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()


async def repl():
    """Interactive REPL loop."""
    console.print(
        Panel.fit(
            "[bold cyan]AIO Agent Platform[/] — Self-evolving AI Agent\n"
            "Type your message and press Enter. Use /help for commands.",
            border_style="cyan",
        )
    )

    while True:
        try:
            user_input = await Prompt.ask("\n[bold green]You[/]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/]")
            break

        if not user_input.strip():
            continue

        if user_input.startswith("/"):
            await handle_command(user_input)
            continue

        # Echo user message
        console.print(f"\n[dim]You:[/] {user_input}")

        # Placeholder: simulate streaming response
        console.print("\n[bold blue]Agent:[/] ", end="")
        # TODO: Connect to agent loop
        console.print("[dim](Agent not yet connected)[/]")
        console.print()


async def handle_command(cmd: str):
    """Handle slash commands."""
    parts = cmd.split()
    command = parts[0].lower()

    match command:
        case "/help":
            console.print("[bold]Available commands:[/]")
            console.print("  /help     — Show this help")
            console.print("  /clear    — Clear screen")
            console.print("  /model    — Show current model")
            console.print("  /quit     — Exit")
        case "/clear":
            console.clear()
        case "/quit" | "/exit":
            sys.exit(0)
        case _:
            console.print(f"[yellow]Unknown command: {command}[/]")


def main():
    """Entry point for `aio-agent` CLI."""
    asyncio.run(repl())


if __name__ == "__main__":
    main()
