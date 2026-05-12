
from kira.legal_sources._common.errors import (
    CorpusUnavailableError,
    EmbeddingUnavailableError,
)
from kira.legal_sources._common.vector_index import VectorSearchHit
from kira.legal_sources.gesetze.schema import (
    SearchNormError,
    SearchNormErrorCode,
    SearchNormInput,
    SearchNormSuccess,
)
from kira.legal_sources.gesetze.search_norm import search_norm


def _make_callables(*, embed_returns=None, embed_raises=None,
                    search_returns=None, search_raises=None):
    calls = {"embed_args": None, "search_kwargs": None}

    def embed(query: str) -> list[float]:
        calls["embed_args"] = query
        if embed_raises:
            raise embed_raises
        return embed_returns or [0.0] * 1024

    def search(*, vector, k, metadata_filter=None):
        calls["search_kwargs"] = {
            "vector": vector,
            "k": k,
            "metadata_filter": metadata_filter,
        }
        if search_raises:
            raise search_raises
        return search_returns or []

    return embed, search, calls


def test_happy_path_returns_hits_in_order():
    hit_a = VectorSearchHit(
        key="bgb-535",
        score=0.94,
        metadata={
            "gesetz": "BGB",
            "paragraph": "535",
            "titel": "Inhalt und Hauptpflichten des Mietvertrags",
            "wortlaut": "(1) Durch den Mietvertrag ...",
            "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
            "stand": "2026-05-09",
        },
    )
    hit_b = VectorSearchHit(
        key="bgb-536",
        score=0.81,
        metadata={
            "gesetz": "BGB",
            "paragraph": "536",
            "titel": "Mietminderung bei Sach- und Rechtsmängeln",
            "wortlaut": "(1) Hat die Mietsache ...",
            "quelle_url": "https://www.gesetze-im-internet.de/bgb/__536.html",
            "stand": "2026-05-09",
        },
    )
    embed, search, calls = _make_callables(search_returns=[hit_a, hit_b])
    inp = SearchNormInput(query="Mietminderung Schimmel", k=5)
    result = search_norm(inp, embed=embed, search=search)
    assert isinstance(result, SearchNormSuccess)
    assert [h.paragraph for h in result.hits] == ["535", "536"]
    assert result.hits[0].score == 0.94
    assert calls["search_kwargs"]["k"] == 5
    assert calls["search_kwargs"]["metadata_filter"] is None


def test_gesetz_filter_translates_to_metadata_filter():
    embed, search, calls = _make_callables(search_returns=[])
    inp = SearchNormInput(
        query="x", gesetz_filter=["BGB", "WEG"]
    )
    search_norm(inp, embed=embed, search=search)
    assert calls["search_kwargs"]["metadata_filter"] == {
        "abkuerzung": {"$in": ["BGB", "WEG"]},
    }


def test_gesetz_filter_preserves_canonical_case():
    """Filter values must NOT be uppercased — vector metadata holds the
    canonical jurabk (`WoEigG`, `BetrKV`), and `$in` is case-sensitive."""
    embed, search, calls = _make_callables(search_returns=[])
    inp = SearchNormInput(query="x", gesetz_filter=["WoEigG", "BetrKV"])
    search_norm(inp, embed=embed, search=search)
    assert calls["search_kwargs"]["metadata_filter"] == {
        "abkuerzung": {"$in": ["WoEigG", "BetrKV"]},
    }


def test_combined_filters_translate_correctly():
    embed, search, calls = _make_callables(search_returns=[])
    inp = SearchNormInput(
        query="x",
        gesetz_filter=["BGB"],
        type_filter=["Gesetz"],
    )
    search_norm(inp, embed=embed, search=search)
    f = calls["search_kwargs"]["metadata_filter"]
    assert f == {
        "abkuerzung": {"$in": ["BGB"]},
        "type": {"$in": ["Gesetz"]},
    }


def test_embedding_failure_returns_error():
    embed, search, _ = _make_callables(
        embed_raises=EmbeddingUnavailableError("bedrock down"),
    )
    inp = SearchNormInput(query="x")
    result = search_norm(inp, embed=embed, search=search)
    assert isinstance(result, SearchNormError)
    assert result.error == SearchNormErrorCode.EMBEDDING_UNAVAILABLE


def test_search_failure_returns_error():
    embed, search, _ = _make_callables(
        search_raises=CorpusUnavailableError("vectors index missing"),
    )
    inp = SearchNormInput(query="x")
    result = search_norm(inp, embed=embed, search=search)
    assert isinstance(result, SearchNormError)
    assert result.error == SearchNormErrorCode.CORPUS_UNAVAILABLE


def test_hit_with_missing_metadata_field_skipped_with_warning(caplog):
    bad = VectorSearchHit(
        key="bgb-535",
        score=0.5,
        metadata={"gesetz": "BGB"},  # missing required fields
    )
    embed, search, _ = _make_callables(search_returns=[bad])
    inp = SearchNormInput(query="x")
    result = search_norm(inp, embed=embed, search=search)
    assert isinstance(result, SearchNormSuccess)
    assert result.hits == []  # bad hit dropped
