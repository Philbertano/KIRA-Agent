"""CLI für KIRA. Nutzbar als `kira ask` oder `kira demo`."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from kira.agent import Agent
from kira.llm import build_client
from kira.llm.models import ModelTier
from kira.router import route

app = typer.Typer(
    name="kira",
    help="KIRA — KI-Junior-Associate für deutsches Mietrecht.",
    no_args_is_help=True,
)
console = Console()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


@app.command()
def ask(
    sachverhalt: Path = typer.Argument(
        ..., exists=True, readable=True, help="Pfad zur Sachverhalts-Datei (Markdown/TXT)."
    ),
    frage: str = typer.Option(..., "--frage", "-q", help="Konkrete Frage an den Agenten."),
    force_tier: str | None = typer.Option(
        None, "--force-tier", help="Modell erzwingen: haiku|sonnet|opus."
    ),
    backend: str = typer.Option("bedrock_eu", "--backend", help="bedrock_eu|anthropic_direct"),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Stellt eine Frage zu einem Sachverhalt."""
    _setup_logging(log_level)
    text = _strip_front_matter(sachverhalt.read_text(encoding="utf-8"))

    forced = ModelTier(force_tier.lower()) if force_tier else None
    routing = route(frage, force_tier=forced)

    console.print(
        Panel.fit(
            f"[bold]Modell:[/bold] {routing.tier.value}\n"
            f"[bold]Aufgabentyp:[/bold] {routing.task_type.value}\n"
            f"[bold]Begründung:[/bold] {routing.reason}",
            title="Routing-Entscheidung",
            border_style="cyan",
        )
    )

    client = build_client(backend=backend)  # type: ignore[arg-type]
    agent = Agent(client=client)
    full_query = f"{frage}\n\n=== Sachverhalt ===\n{text}"
    result = agent.run(full_query, routing=routing)

    if result.tool_calls:
        table = Table(title="Tool-Aufrufe", show_lines=False)
        table.add_column("#", justify="right")
        table.add_column("Tool")
        table.add_column("Input")
        table.add_column("OK")
        for idx, call in enumerate(result.tool_calls, 1):
            table.add_row(
                str(idx),
                call["tool"],
                str(call["input"])[:60],
                "[red]✗[/red]" if call["is_error"] else "[green]✓[/green]",
            )
        console.print(table)

    console.print(Panel(Markdown(result.final_text), title="Antwort", border_style="green"))


@app.command()
def demo(
    backend: str = typer.Option("bedrock_eu", "--backend"),
    force_tier: str | None = typer.Option(None, "--force-tier"),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Führt den Beispielsachverhalt 'Mietminderung Schimmel' aus."""
    sachverhalt = (
        Path(__file__).resolve().parent.parent.parent
        / "data"
        / "beispielsachverhalte"
        / "001_mietminderung_schimmel.md"
    )
    ask(
        sachverhalt=sachverhalt,
        frage="Bitte rechtliche Würdigung des Mietminderungsanspruchs und Empfehlung für nächste Schritte.",
        force_tier=force_tier,
        backend=backend,
        log_level=log_level,
    )


# --- Helpers ---


def _strip_front_matter(raw: str) -> str:
    if not raw.startswith("---"):
        return raw
    end = raw.find("---", 3)
    if end == -1:
        return raw
    return raw[end + 3 :].lstrip()


if __name__ == "__main__":
    app()
