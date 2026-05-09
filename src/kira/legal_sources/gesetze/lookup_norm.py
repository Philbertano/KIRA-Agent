"""Pure function: resolve a single paragraph from an in-memory corpus."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta

from kira.legal_sources.gesetze.corpus_format import GesetzKorpus, Norm
from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormInput,
    LookupNormResult,
    LookupNormSuccess,
)

_STAND_WARN_AGE = timedelta(days=30)


def lookup_norm(
    input_data: LookupNormInput,
    *,
    corpus: Mapping[str, GesetzKorpus],
    today: date | None = None,
) -> LookupNormResult:
    """Resolve `input_data` against the in-memory `corpus`.

    `corpus` is a mapping of lower-case Gesetz-Abkürzung → parsed `GesetzKorpus`.
    `today` is injectable for deterministic stand-warning tests.
    """
    today = today or date.today()
    abk = input_data.gesetz  # already lower-case after validation
    korpus = corpus.get(abk)
    if korpus is None:
        return LookupNormError(
            error=LookupNormErrorCode.UNKNOWN_GESETZ,
            message=f"Gesetz {abk.upper()!r} ist nicht im Korpus geladen.",
            gesetz=abk.upper(),
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    norm = korpus.paragraphen.get(input_data.paragraph)
    if norm is None:
        return LookupNormError(
            error=LookupNormErrorCode.PARAGRAPH_NOT_FOUND,
            message=(
                f"§ {input_data.paragraph} {abk.upper()} ist nicht im kuratierten Korpus "
                f"({', '.join(korpus.meta.gefiltert_auf) or 'leer'})."
            ),
            gesetz=abk.upper(),
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    wortlaut, used_absatz = _select_text(norm, input_data.absatz)
    if input_data.absatz is not None and used_absatz is None:
        return LookupNormError(
            error=LookupNormErrorCode.ABSATZ_NOT_FOUND,
            message=(
                f"Absatz {input_data.absatz} in § {input_data.paragraph} "
                f"{abk.upper()} existiert nicht."
            ),
            gesetz=abk.upper(),
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    return LookupNormSuccess(
        gesetz=korpus.meta.abkuerzung,
        gesetz_titel=korpus.meta.titel,
        paragraph=norm.paragraph,
        absatz=used_absatz,
        titel=norm.titel,
        wortlaut=wortlaut,
        stand=korpus.meta.stand,
        quelle_url=norm.quelle_url or korpus.meta.quelle_url,
        stand_warnung=_stand_warning(korpus.meta.stand, today),
    )


def _select_text(norm: Norm, absatz: str | None) -> tuple[str, str | None]:
    if absatz is None:
        if not norm.absaetze:
            return ("", None)
        joined = "\n\n".join(f"({a.nummer}) {a.text}" for a in norm.absaetze)
        return (joined, None)
    for a in norm.absaetze:
        if a.nummer == absatz:
            return (f"({a.nummer}) {a.text}", a.nummer)
    return ("", None)


def _stand_warning(stand: str, today: date) -> str | None:
    try:
        stand_date = datetime.strptime(stand, "%Y-%m-%d").date()
    except ValueError:
        return f"Stand-Datum {stand!r} ist unleserlich — Korpus prüfen."
    age = today - stand_date
    if age > _STAND_WARN_AGE:
        return (
            f"Korpus-Stand ist {age.days} Tage alt (Schwelle: {_STAND_WARN_AGE.days} Tage). "
            f"Manuell verifizieren oder Ingest erneut ausführen."
        )
    return None
