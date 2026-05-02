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
from kira.pseudonymizer import EntityKind, Gender, Party, Role
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
    """Stellt eine Frage zu einem Sachverhalt mit fest verdrahteten Beispiel-Parteien.

    Phase 1: Parteien werden in der Sachverhalts-Datei via YAML-Front-Matter
    erwartet (siehe data/beispielsachverhalte/). Phase 2: interaktive Eingabe
    oder Mandats-Datenbank.
    """
    _setup_logging(log_level)
    parties = _parse_parties_from_file(sachverhalt)
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
    result = agent.run(full_query, parties=parties, routing=routing)

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
def ingest(
    gesetze: list[str] = typer.Argument(
        None,
        help=(
            "Gesetzes-Abkürzungen, die geladen werden sollen "
            "(z.B. bgb betrkv heizkostenv). Ohne Argument: alle bekannten."
        ),
    ),
    output_dir: Path = typer.Option(
        None, "--output-dir", help="Wohin die JSON-Korpora geschrieben werden."
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Lädt Gesetzes-Korpora von gesetze-im-internet.de und speichert sie lokal.

    Aktualisiert den lokalen Overlay-Korpus, der vom Agent bevorzugt vor den
    im Package gebündelten kuratierten JSONs verwendet wird.
    """
    _setup_logging(log_level)
    from kira.knowledge.ingest import GESETZE, ingest as do_ingest

    if gesetze:
        unbekannt = [g for g in gesetze if g.lower() not in GESETZE]
        if unbekannt:
            verfuegbar = ", ".join(GESETZE.keys())
            console.print(
                f"[red]Unbekannte Gesetze: {unbekannt}.[/red] Verfügbar: {verfuegbar}"
            )
            raise typer.Exit(code=1)

    console.print(Panel.fit(
        f"Lade {gesetze or list(GESETZE.keys())} von gesetze-im-internet.de…",
        border_style="cyan",
    ))
    try:
        written = do_ingest(gesetze, output_dir=output_dir)
    except Exception as exc:
        console.print(f"[red]FEHLER:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    table = Table(title="Geladene Gesetze")
    table.add_column("Abkürzung")
    table.add_column("Pfad")
    for abk, path in written.items():
        table.add_row(abk, str(path))
    console.print(table)


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


@app.command(name="check-pseudonymisierung")
def check_pseudo(
    sachverhalt: Path = typer.Argument(..., exists=True),
) -> None:
    """Zeigt nur die Pseudonymisierung — kein LLM-Call. Für Audit."""
    parties = _parse_parties_from_file(sachverhalt)
    text = _strip_front_matter(sachverhalt.read_text(encoding="utf-8"))

    from kira.pseudonymizer import Pseudonymizer, check_for_leaks

    pseudo = Pseudonymizer(parties=parties)
    result = pseudo.process(text)
    leaks = check_for_leaks(result.text, result.parties)

    console.print(Panel(result.text, title="Pseudonymisierter Text", border_style="cyan"))

    if leaks:
        console.print(
            Panel(
                "\n".join(f"  - {label}: {value!r}" for label, value in leaks.leaks),
                title="[red]LEAK-WARNUNGEN[/red]",
                border_style="red",
            )
        )
    else:
        console.print(Panel("Keine Leaks erkannt.", border_style="green"))

    table = Table(title="Mapping (lokal, nicht in Cloud)")
    table.add_column("Platzhalter")
    table.add_column("Klarwert")
    for placeholder, real in result.mapping.items():
        table.add_row(placeholder, real)
    console.print(table)


# --- Helpers ---


def _parse_parties_from_file(path: Path) -> list[Party]:
    """Liest YAML-ähnliches Front-Matter mit Parteien-Definition.

    Format::

        ---
        parties:
          - name: Klaus Müller
            role: MIETER
            gender: m
            kind: nat
            age_band: 60-69
          - name: ABC Immobilien GmbH
            role: VERMIETER
            kind: jur
        ---
    """
    import yaml  # lazy: pyyaml ist eigentlich keine Dep, fallen back zu manuellem Parser

    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise typer.BadParameter(
            f"Sachverhalts-Datei {path} braucht YAML-Front-Matter mit 'parties:'."
        )

    end = raw.find("---", 3)
    if end == -1:
        raise typer.BadParameter("Front-Matter nicht geschlossen (erwartet zweites ---).")

    front = raw[3:end].strip()
    try:
        data = yaml.safe_load(front)
    except Exception as exc:
        raise typer.BadParameter(f"Front-Matter nicht parsebar: {exc}") from exc

    parties = []
    for entry in data.get("parties", []):
        parties.append(
            Party(
                real_name=entry["name"],
                role=Role(entry["role"]),
                kind=EntityKind(entry.get("kind", "nat")),
                gender=Gender(entry.get("gender", "u")),
                age_band=entry.get("age_band"),
                aliases=entry.get("aliases", []),
            )
        )
    return parties


def _strip_front_matter(raw: str) -> str:
    if not raw.startswith("---"):
        return raw
    end = raw.find("---", 3)
    if end == -1:
        return raw
    return raw[end + 3 :].lstrip()


if __name__ == "__main__":
    app()
