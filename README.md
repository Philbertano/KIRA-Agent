# KIRA — KI-Junior-Associate für deutsches Mietrecht

KIRA ist ein juristischer Assistenz-Agent für Anwaltskanzleien mit Schwerpunkt
Mietrecht. Sie arbeitet einem zugelassenen Rechtsanwalt zu, der die Ergebnisse
prüft und verantwortet (Junior-Associate-Pattern, RDG-konform).

## Designprinzipien

1. **Anwalt im Loop.** KIRA macht Vorarbeit — Recherche, Sachverhalts-Extraktion,
   Schriftsatz-Entwürfe. Der Anwalt prüft und gibt frei.
2. **Ausschließlich deutsches Recht.** Der System-Prompt verbietet ausländische
   Analogien explizit. Web-Tools nutzen eine Domain-Whitelist.
3. **Kein Halluzinieren.** §§ und Aktenzeichen werden ausschließlich über
   Tools nachgeschlagen, nie aus dem Modell-Gedächtnis.
4. **Defense-in-Depth Datenschutz.** Pseudonymisierung *vor* jedem LLM-Call,
   zusätzlich zum EU-Hosting (AWS Bedrock Frankfurt).
5. **Intelligenter Modell-Router.** Haiku für Klassifikation, Sonnet für
   Standard-Aufgaben, Opus für komplexe Würdigung. Anwalt kann jederzeit
   überstimmen.

## Rechtlicher Rahmen

KIRA ist so gebaut, dass sie folgende Anforderungen erfüllen kann (Setup-
Konfiguration durch Kanzlei nötig):

- **§ 43e BRAO** — Inanspruchnahme von Dienstleistungen: Cloud-Nutzung erfordert
  schriftlichen Vertrag mit Verschwiegenheitsverpflichtung. AWS bietet Standard-
  AVV nach DSGVO; Anthropic ist Subprocessor unter dem AWS-DPA.
- **§ 203 StGB Abs. 3** — Erlaubnis zur Datenweitergabe an „mitwirkende Personen"
  (IT-Dienstleister) seit 2017.
- **DSGVO Art. 28 / 35** — AVV mit AWS, DSFA für Mandantendaten ist faktisch
  Pflicht. Pseudonymisierungs-Layer reduziert die zu schützenden Daten weiter.
- **BRAK-Leitfaden 12/2024** — KI-Tools dürfen Daten *nicht* zum Training
  verwenden. Bedrock erfüllt das vertraglich.

## Architektur

```
src/kira/
├── agent/             Agent-Loop + Tools (lookup_norm, fetch_urteil, frist)
├── pseudonymizer/     Strukturierte Platzhalter, Leakage-Check, Mapping-Store
├── router/            Regelbasiertes + Haiku-gestütztes Modell-Routing
├── llm/               Backend-Abstraktion (Bedrock EU / Direct API)
└── knowledge/         Statische Gesetzestexte (BGB Mietrecht)
```

Die Pseudonymisierungs-Pipeline verwendet **strukturierte Platzhalter**:

```
"Klaus Müller, 64, mietet von ABC Immobilien GmbH"
                    ↓
"[MIETER_1:m,nat,~60-69] mietet von [VERMIETER_1:u,jur]"
```

Der Agent sieht: **Rolle, Geschlecht, Person-Typ, Altersband** — also alles, was
für die rechtliche Würdigung relevant ist. Klarnamen verlassen den lokalen
Server nie.

Vor jedem LLM-Call läuft ein **Leakage-Check**: regex-basierter Scan auf IBAN,
E-Mail, Telefonnummern, Klarnamen aus dem Mandanten-Setup. Findet er etwas,
wird der Call **hart abgebrochen**.

## Setup

```bash
# Python 3.11+
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# AWS-Credentials (Standard-Resolver: ~/.aws/credentials, AWS_PROFILE, oder Env)
export AWS_REGION=eu-central-1
# Modell-Access ist seit Mitte 2025 in Bedrock automatisch freigeschaltet.
```

## Verwendung

```bash
# Pseudonymisierung prüfen (kein LLM-Call)
kira check-pseudonymisierung data/beispielsachverhalte/001_mietminderung_schimmel.md

# Beispielsachverhalt durchspielen (nutzt Bedrock EU)
kira demo

# Eigene Frage zu einem Sachverhalt
kira ask data/beispielsachverhalte/001_mietminderung_schimmel.md \
  --frage "Berechne die Mietminderungsquote und entwirf ein Schreiben."

# Modell explizit erzwingen
kira ask … --force-tier opus

# Aktuelle Gesetzes-Korpora von gesetze-im-internet.de laden
kira ingest                      # alle bekannten (bgb, betrkv, heizkostenv)
kira ingest bgb                  # gezielt
kira ingest --output-dir ./data/gesetze
```

### Gesetzes-Korpus

KIRA versucht zuerst, Gesetze aus `./data/gesetze/<abk>.json` zu laden
(Overlay, vom `kira ingest`-Befehl geschrieben). Findet sie dort nichts,
fällt sie auf den im Package gebündelten **kuratierten Korpus** zurück:

| Gesetz | Quelle | Subset |
|---|---|---|
| BGB | gesetze-im-internet.de | §§ 195, 199, 286 (Verjährung/Verzug); §§ 535–580a (Mietrecht) |
| BetrKV | gesetze-im-internet.de | komplett |
| HeizkostenV | gesetze-im-internet.de | komplett |

Wenn der lokale Korpus älter als 6 Monate ist, warnt jedes Tool im Output —
der Anwalt sieht sofort, dass er `kira ingest` neu ausführen sollte.

### Sachverhalts-Format

Sachverhalt als Markdown mit YAML-Front-Matter, das die Parteien strukturiert
benennt:

```markdown
---
mandat_id: 2026-001
parties:
  - name: Klaus Müller
    role: MIETER          # MIETER | VERMIETER | MITMIETER | BUERGE | …
    gender: m             # m | w | d | u
    kind: nat             # nat (natürlich) | jur (juristisch)
    age_band: 60-69       # optional, relevant für § 574 BGB
    aliases: ["Herr Müller", "K. Müller"]
---

Freitext-Sachverhalt …
```

## Routing-Tabelle

| Aufgabentyp | Modell | Begründung |
|---|---|---|
| Sachverhalt extrahieren | Haiku 4.5 | Strukturextraktion |
| Norm-Lookup, Frist | Haiku 4.5 | klar definiert |
| Schriftsatz aus Vorlage | Sonnet 4.6 | Default-Workhorse |
| Recherche & Zusammenfassung | Sonnet 4.6 | gutes Reasoning |
| Sachverhalts-Vergleich | Sonnet 4.6 | mehrstufiges Tool-Use |
| Rechtliche Würdigung | Opus 4.6 | tiefes Reasoning |
| Vollumfängliches Gutachten | Opus 4.6 | mehrere Anspruchsgrundlagen |

Eskalation: Wenn eine Sonnet-Aufgabe sehr komplex aussieht (lang, viele
Konjunktionen, mehrere Fragen), routet der Router automatisch zu Opus.

## Tools, die der Agent verwendet

| Tool | Quelle | Zweck |
|---|---|---|
| `lookup_norm` | lokaler Multi-Gesetz-Korpus (BGB, BetrKV, HeizkostenV) | Norm im Wortlaut, mit Stand + Quellen-URL |
| `search_norm` | lokaler Korpus, Volltext | Stichwort-Suche, wenn Norm noch unbekannt |
| `list_normen` | lokaler Korpus | Inhaltsverzeichnis / Übersicht der Gesetze |
| `search_rechtsprechung` | openjur.de (Whitelist) | Urteils-Suche |
| `fetch_urteil` | rechtsprechung-im-internet.de, openjur.de, dejure.org, BGH | Volltext eines Urteils |
| `berechne_frist` | deterministisch in Python | Kündigung, Widerspruch, Verjährung |

Die Web-Tools haben eine **strikte Domain-Whitelist**. Andere URLs werden
abgewiesen — damit kann der Agent niemals auf österreichische, schweizerische
oder fragwürdige Quellen ausweichen.

## Roadmap

**Phase 1 (jetzt):** CLI, Pseudonymisierung, Routing, drei Tools, ein Beispielfall.

**Phase 2:** Wissensbasis-Pipeline — Ingest eigener Sachverhalte (PDF/DOCX) in
Vektor-DB, Coreference-Resolution, Presidio-Integration für tiefere PII-Erkennung,
Caching-Layer für Rechtsprechung.

**Phase 3:** FastAPI-Backend, Audit-Log mit Mandatszuordnung, Teams-Bot via
Bot Framework SDK, Outlook-Add-In.

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

Schwerpunkt der Tests: **Pseudonymizer-Leakage**. Wenn diese Tests rot sind,
gehen potenziell Klardaten an die Cloud — nicht deployen.

## Verantwortung

KIRA ist kein Anwalt. Jede juristische Aussage ist Vorarbeit, die der
zugelassene Rechtsanwalt prüfen, bewerten und freigeben muss.
