"""Invoke the three deployed legal-sources Lambdas and pretty-print results.

Usage examples:

    # Look up a specific paragraph
    python scripts/invoke_legal_lambdas.py lookup BGB 535
    python scripts/invoke_legal_lambdas.py lookup WoEigG 14
    python scripts/invoke_legal_lambdas.py lookup BetrKV 2 --absatz 1

    # Semantic search
    python scripts/invoke_legal_lambdas.py search "Mietminderung wegen Schimmel"
    python scripts/invoke_legal_lambdas.py search "Betriebskosten" --gesetz BetrKV
    python scripts/invoke_legal_lambdas.py search "Wohnungseigentum" -k 5

    # Trigger a manual ingest run
    python scripts/invoke_legal_lambdas.py ingest

    # Run the canned smoke test (lookup + search across a few laws)
    python scripts/invoke_legal_lambdas.py demo

Add --full to print untruncated wortlaut, --raw to dump the JSON envelope.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from typing import Any

import boto3

REGION = "eu-central-1"
LOOKUP_FN = "kira-legal-lookup-norm"
SEARCH_FN = "kira-legal-search"
INGEST_FN_TAG = "Ingest"

_GREEN = "\033[92m"
_BLUE = "\033[94m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def main() -> int:
    args = _build_parser().parse_args()
    lambda_client = boto3.client("lambda", region_name=REGION)

    if args.cmd == "lookup":
        return _cmd_lookup(lambda_client, args)
    if args.cmd == "search":
        return _cmd_search(lambda_client, args)
    if args.cmd == "ingest":
        return _cmd_ingest(lambda_client, args)
    if args.cmd == "demo":
        return _cmd_demo(lambda_client, args)
    return 2


def _cmd_lookup(client: Any, args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"gesetz": args.gesetz, "paragraph": args.paragraph}
    if args.absatz is not None:
        payload["absatz"] = args.absatz
    result = _invoke(client, LOOKUP_FN, payload, args)
    if result is None:
        return 1
    _render_lookup(result, full=args.full)
    return 0


def _cmd_search(client: Any, args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"query": args.query, "k": args.k}
    if args.gesetz:
        payload["gesetz_filter"] = args.gesetz
    if args.type:
        payload["type_filter"] = args.type
    result = _invoke(client, SEARCH_FN, payload, args)
    if result is None:
        return 1
    _render_search(result, full=args.full)
    return 0


def _cmd_ingest(client: Any, args: argparse.Namespace) -> int:
    fn_name = _discover_ingest_fn(client)
    if not fn_name:
        print(f"{_RED}No ingest function found in {REGION}{_RESET}")
        return 1
    print(f"{_DIM}Triggering {fn_name} — this may take several minutes…{_RESET}")
    t0 = time.time()
    result = _invoke(client, fn_name, {}, args, invocation_type="RequestResponse")
    if result is None:
        return 1
    elapsed = time.time() - t0
    print(f"\n{_BOLD}Ingest summary{_RESET} (took {elapsed:.1f}s)")
    print(f"  written: {len(result.get('written', []))}")
    print(f"  skipped: {len(result.get('skipped', []))}")
    print(f"  errors:  {len(result.get('errors', []))}")
    for e in result.get("errors", [])[:10]:
        print(f"    - {e.get('abkuerzung')}: {e.get('error')}")
    if len(result.get("errors", [])) > 10:
        print(f"    … and {len(result['errors']) - 10} more")
    return 0


def _cmd_demo(client: Any, args: argparse.Namespace) -> int:
    print(f"{_BOLD}== Lookup tests =={_RESET}\n")
    for gesetz, paragraph in [
        ("BGB", "535"),
        ("BGB", "536"),
        ("WoEigG", "14"),
        ("BetrKV", "2"),
        ("weg", "14"),  # should be unknown_gesetz (no alias yet)
    ]:
        print(f"{_BLUE}→ lookup {gesetz} §{paragraph}{_RESET}")
        result = _invoke(client, LOOKUP_FN, {"gesetz": gesetz, "paragraph": paragraph}, args)
        if result:
            _render_lookup(result, full=False, compact=True)
        print()

    print(f"\n{_BOLD}== Search tests =={_RESET}\n")
    for query, filt in [
        ("Mietminderung wegen Schimmel", None),
        ("Betriebskosten", ["BetrKV"]),
        ("Wohnungseigentum", ["WoEigG"]),
        ("Kündigung des Mietverhältnisses", None),
    ]:
        label = f"'{query}'"
        if filt:
            label += f" (filter={filt})"
        print(f"{_BLUE}→ search {label}{_RESET}")
        payload: dict[str, Any] = {"query": query, "k": 3}
        if filt:
            payload["gesetz_filter"] = filt
        result = _invoke(client, SEARCH_FN, payload, args)
        if result:
            _render_search(result, full=False, compact=True)
        print()
    return 0


def _invoke(
    client: Any,
    fn_name: str,
    payload: dict[str, Any],
    args: argparse.Namespace,
    invocation_type: str = "RequestResponse",
) -> dict[str, Any] | None:
    resp = client.invoke(
        FunctionName=fn_name,
        InvocationType=invocation_type,
        Payload=json.dumps(payload).encode("utf-8"),
    )
    raw_body = resp["Payload"].read()
    if resp.get("FunctionError"):
        print(f"{_RED}Function error: {resp['FunctionError']}{_RESET}")
        print(raw_body.decode("utf-8", errors="replace"))
        return None
    envelope = json.loads(raw_body.decode("utf-8"))
    if getattr(args, "raw", False):
        print(json.dumps(envelope, indent=2, ensure_ascii=False))
    if envelope.get("isError"):
        text = envelope.get("content", [{}])[0].get("text", "")
        try:
            err = json.loads(text)
            print(f"{_RED}{err.get('error')}: {err.get('message', text)}{_RESET}")
        except json.JSONDecodeError:
            print(f"{_RED}{text}{_RESET}")
        return None
    text = envelope.get("content", [{}])[0].get("text", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Ingest returns the raw dict, not the MCP envelope.
        return envelope


def _render_lookup(r: dict[str, Any], *, full: bool, compact: bool = False) -> None:
    head = f"  {_GREEN}{r['gesetz']} §{r['paragraph']}{_RESET}"
    if r.get("titel"):
        head += f"  {_BOLD}{r['titel']}{_RESET}"
    print(head)
    if r.get("stand_warnung"):
        print(f"  {_YELLOW}⚠ {r['stand_warnung']}{_RESET}")
    wortlaut = r.get("wortlaut") or ""
    if not full and compact:
        wortlaut = _shorten(wortlaut, 200)
    elif not full:
        wortlaut = _shorten(wortlaut, 800)
    for line in wortlaut.split("\n"):
        for wrapped in textwrap.wrap(line, width=96, subsequent_indent="     ") or [""]:
            print(f"     {wrapped}")
    print(f"  {_DIM}Quelle: {r.get('quelle_url')}  (Stand: {r.get('stand')}){_RESET}")


def _render_search(r: dict[str, Any], *, full: bool, compact: bool = False) -> None:
    hits = r.get("hits", [])
    if not hits:
        print(f"  {_YELLOW}Keine Treffer{_RESET}")
        return
    for i, h in enumerate(hits, 1):
        score = h.get("score", 0.0)
        bar = _bar(score)
        print(f"  {_GREEN}{i}. {h['gesetz']} §{h['paragraph']}{_RESET}  "
              f"{_BOLD}{h.get('titel','')}{_RESET}  {_DIM}{bar} {score:.3f}{_RESET}")
        wortlaut = h.get("wortlaut") or ""
        if not full:
            wortlaut = _shorten(wortlaut, 160 if compact else 400)
        for line in wortlaut.split("\n"):
            for wrapped in textwrap.wrap(line, width=94, subsequent_indent="       ") or [""]:
                print(f"       {wrapped}")
        if h.get("quelle_url"):
            print(f"       {_DIM}{h['quelle_url']}{_RESET}")


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _bar(score: float) -> str:
    filled = max(0, min(10, int(round(score * 10))))
    return "█" * filled + "░" * (10 - filled)


def _discover_ingest_fn(client: Any) -> str | None:
    paginator = client.get_paginator("list_functions")
    for page in paginator.paginate():
        for fn in page.get("Functions", []):
            if INGEST_FN_TAG in fn["FunctionName"] and "kira" in fn["FunctionName"].lower():
                return fn["FunctionName"]
    return None


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Invoke KIRA legal-sources Lambdas",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("lookup", help="Look up a specific § by gesetz + paragraph")
    pl.add_argument("gesetz", help="Canonical jurabk, e.g. BGB, WoEigG, BetrKV")
    pl.add_argument("paragraph", help="Paragraph number, e.g. 535 or 535a")
    pl.add_argument("--absatz", help="Optional absatz, e.g. '1'")
    _common_flags(pl)

    ps = sub.add_parser("search", help="Semantic search over the full corpus")
    ps.add_argument("query", help="Natural-language query (German)")
    ps.add_argument("-k", type=int, default=10, help="Number of hits (1-50, default 10)")
    ps.add_argument("--gesetz", nargs="+", help="Limit to one or more abkuerzungen, e.g. --gesetz BGB WoEigG")
    ps.add_argument("--type", nargs="+", choices=["Gesetz", "Verordnung"],
                    help="Limit to Gesetz, Verordnung, or both")
    _common_flags(ps)

    pi = sub.add_parser("ingest", help="Trigger a manual ingest run (synchronous)")
    _common_flags(pi)

    pd = sub.add_parser("demo", help="Run a canned smoke test across multiple laws")
    _common_flags(pd)

    return p


def _common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--full", action="store_true", help="Print full wortlaut (no truncation)")
    p.add_argument("--raw", action="store_true", help="Also dump the raw Lambda envelope")


if __name__ == "__main__":
    sys.exit(main())
