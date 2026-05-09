from kira.legal_sources.gesetze.corpus_format import GesetzKorpus


def test_parses_valid_corpus_payload():
    payload = {
        "_meta": {
            "abkuerzung": "BGB",
            "titel": "Bürgerliches Gesetzbuch",
            "stand": "2026-05-08",
            "quelle": "gesetze-im-internet.de",
            "quelle_url": "https://www.gesetze-im-internet.de/bgb",
            "gefiltert_auf": ["§§ 194–580a"],  # noqa: RUF001
            "anzahl_normen": 1,
        },
        "paragraphen": {
            "535": {
                "paragraph": "535",
                "titel": "Inhalt und Hauptpflichten des Mietvertrags",
                "absaetze": [
                    {"nummer": "1", "text": "Durch den Mietvertrag …"},
                    {"nummer": "2", "text": "Der Vermieter …"},
                ],
                "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
            }
        },
    }
    korpus = GesetzKorpus.model_validate(payload)
    assert korpus.meta.abkuerzung == "BGB"
    assert "535" in korpus.paragraphen
    assert korpus.paragraphen["535"].absaetze[0].nummer == "1"


def test_lookup_paragraph_returns_none_for_missing():
    payload = {
        "_meta": {
            "abkuerzung": "BGB",
            "titel": "x",
            "stand": "2026-05-08",
            "quelle": "x",
            "quelle_url": "https://example.test",
            "gefiltert_auf": [],
            "anzahl_normen": 0,
        },
        "paragraphen": {},
    }
    korpus = GesetzKorpus.model_validate(payload)
    assert korpus.paragraphen.get("999") is None
