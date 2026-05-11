"""Pure function: resolve a single paragraph via injected loaders."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta

from kira.legal_sources.gesetze.corpus_format import GesetzMeta, Norm
from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormInput,
    LookupNormResult,
    LookupNormSuccess,
)

_STAND_WARN_AGE = timedelta(days=30)
_NEAR_MISS_K = 5

LoadMetaFn = Callable[[str], GesetzMeta | None]
LoadNormFn = Callable[[str, str], Norm | None]


def lookup_norm(
    input_data: LookupNormInput,
    *,
    load_meta: LoadMetaFn,
    load_norm: LoadNormFn,
    today: date | None = None,
) -> LookupNormResult:
    today = today or date.today()
    abk = input_data.gesetz  # already lower-case after validation

    meta = load_meta(abk)
    if meta is None:
        return LookupNormError(
            error=LookupNormErrorCode.UNKNOWN_GESETZ,
            message=f"Gesetz {abk.upper()!r} ist nicht im Korpus.",
            gesetz=abk.upper(),
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    entry = meta.paragraphen.get(input_data.paragraph)
    if entry is None:
        return LookupNormError(
            error=LookupNormErrorCode.PARAGRAPH_NOT_FOUND,
            message=(
                f"§ {input_data.paragraph} {meta.abkuerzung} ist nicht im Korpus. "
                f"Nahe Treffer: {', '.join(_near_misses(input_data.paragraph, meta))}."
            ),
            gesetz=meta.abkuerzung,
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    norm = load_norm(entry.key.split("/")[-2], entry.key)
    if norm is None:
        return LookupNormError(
            error=LookupNormErrorCode.CORPUS_UNAVAILABLE,
            message=f"Konnte {entry.key} nicht laden.",
            gesetz=meta.abkuerzung,
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    wortlaut, used_absatz = _select_text(norm, input_data.absatz)
    if input_data.absatz is not None and used_absatz is None:
        return LookupNormError(
            error=LookupNormErrorCode.ABSATZ_NOT_FOUND,
            message=(
                f"Absatz {input_data.absatz} in § {input_data.paragraph} "
                f"{meta.abkuerzung} existiert nicht."
            ),
            gesetz=meta.abkuerzung,
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    return LookupNormSuccess(
        gesetz=meta.abkuerzung,
        gesetz_titel=meta.titel,
        paragraph=norm.paragraph,
        absatz=used_absatz,
        titel=norm.titel,
        wortlaut=wortlaut,
        stand=meta.stand,
        quelle_url=norm.quelle_url or meta.quelle_url,
        stand_warnung=_stand_warning(meta.stand, today),
    )


def _select_text(norm: Norm, absatz: str | None) -> tuple[str, str | None]:
    if absatz is None:
        if not norm.absaetze:
            return ("", None)
        return ("\n\n".join(f"({a.nummer}) {a.text}" for a in norm.absaetze), None)
    for a in norm.absaetze:
        if a.nummer == absatz:
            return (f"({a.nummer}) {a.text}", a.nummer)
    return ("", None)


def _stand_warning(stand: str, today: date) -> str | None:
    try:
        stand_date = datetime.strptime(stand, "%Y-%m-%d").date()
    except ValueError:
        return f"Stand-Datum {stand!r} ist unleserlich."
    age = today - stand_date
    if age > _STAND_WARN_AGE:
        return f"Korpus-Stand ist {age.days} Tage alt — bitte verifizieren."
    return None


def _near_misses(target: str, meta: GesetzMeta) -> list[str]:
    """Return up to _NEAR_MISS_K paragraph keys numerically/lexically closest to target."""
    keys = list(meta.paragraphen.keys())
    target_num = _to_sort_key(target)
    keys.sort(key=lambda k: abs(_to_sort_key(k) - target_num))
    return keys[:_NEAR_MISS_K]


def _to_sort_key(p: str) -> float:
    """Coerce '535', '535a', '535b' into sortable numbers (suffix as 0.01-step)."""
    import re
    m = re.match(r"^(\d+)([a-zA-Z]?)$", p)
    if not m:
        return 0.0
    num = int(m.group(1))
    suffix = m.group(2)
    return num + (ord(suffix.lower()) - ord("a") + 1) * 0.01 if suffix else float(num)
