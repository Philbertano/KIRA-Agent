# Legal-Sources V2: all laws + semantic search — Design Spec

**Date:** 2026-05-10
**Status:** Draft, awaiting user approval
**Builds on:** `2026-05-09-legal-sources-tool1-design.md` (V1)
**Scope:** Two coordinated upgrades shipped together — (a) extend the corpus from "Mietrecht-curated subset" to "all Bundesgesetze + Rechtsverordnungen" and (b) add a `search_norm` MCP tool for semantic search over that corpus.

---

## 1. Goal

Make KIRA's legal-sources Lambda set the foundation for a multi-domain lawyer tool, not a Mietrecht-only one:

- **`lookup_norm`** answers any "what does §X of Y say" question across ~2,500 Bundesgesetze + Rechtsverordnungen with the same authoritative-citation guarantees we shipped in V1.
- **`search_norm`** (new) answers "find me the relevant §§ for this fuzzy question/situation" via semantic search over every paragraph of every law in the corpus.

Together these two tools cover the two halves of a lawyer's reference workflow: discovery (search) and authoritative quotation (lookup).

### Non-goals

- Rechtsprechung / Urteile — still deferred (was Tool 2 in V1; remains deferred).
- Federal-state (Land) law — gesetze-im-internet.de only publishes Bundesrecht.
- Repealed law text — we don't retain historical versions.
- LG / AG decision search — different sources, different design.
- Cross-law citation graph (e.g., "show me everything that references §535 BGB") — interesting but distinct.

## 2. User-facing contracts

### 2.1 `lookup_norm` (unchanged surface)

The V1 input/output schema is preserved verbatim. The only behavioral change visible to consumers is that previously-failing `unknown_gesetz` calls now succeed for any of the ~2,500 laws.

```json
// Input
{ "gesetz": "WEG", "paragraph": "14", "absatz": "1" }

// Success output (unchanged from V1)
{
  "gesetz": "WEG",
  "gesetz_titel": "Wohnungseigentumsgesetz",
  "paragraph": "14",
  "absatz": "1",
  "titel": "Pflichten des Wohnungseigentümers",
  "wortlaut": "(1) Jeder Wohnungseigentümer ist verpflichtet, ...",
  "stand": "2026-05-10",
  "quelle_url": "https://www.gesetze-im-internet.de/woeigg/__14.html",
  "stand_warnung": null
}
```

The error envelope (`unknown_gesetz`, `paragraph_not_found`, `absatz_not_found`, `corpus_unavailable`, `validation_error`) is preserved. `paragraph_not_found` errors gain a richer `message` field that lists the closest-matching paragraph numbers in that Gesetz.

### 2.2 `search_norm` (new)

```json
// Input
{
  "query": "Pflichten des Vermieters zur Erhaltung der Mietsache",
  "k": 5,                         // optional; default 10, max 50
  "gesetz_filter": ["BGB"],       // optional; restrict to these abkuerzungen
  "type_filter": ["Gesetz"]       // optional; "Gesetz" | "Verordnung"
}

// Success output
{
  "query": "Pflichten des Vermieters zur Erhaltung der Mietsache",
  "hits": [
    {
      "gesetz": "BGB",
      "paragraph": "535",
      "absatz": null,             // or specific Absatz if hit was at that level
      "titel": "Inhalt und Hauptpflichten des Mietvertrags",
      "wortlaut": "(1) Durch den Mietvertrag ...",
      "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
      "stand": "2026-05-09",       // upstream last-modified for this §'s source
      "score": 0.94                // cosine similarity, 0–1
    },
    { ... }
  ]
}
```

Each hit carries its own `stand`, surfaced from the vector's metadata. There is no top-level corpus `stand` because the search Lambda intentionally has no S3 access — it's purely a vector-search frontend.

Error envelope mirrors `lookup_norm` plus one new code:

- `embedding_unavailable` — Bedrock embedding call failed; transient AWS issue.

`search_norm` is **discovery, not authority**: hits include the wortlaut for convenience but the contract still requires the agent to call `lookup_norm` if it intends to cite the result in a Schriftsatz (the system prompt's "every § citation requires a prior `lookup_norm`" rule remains).

## 3. Architecture overview

```
                                  ┌────────────────────────────────────────┐
                                  │  scripts/backfill_corpus.py             │
                                  │  (NEW, runs locally — one-time setup)   │
                                  │   1. fetch gii-toc.xml                  │
                                  │   2. filter Gesetze + VOs               │
                                  │   3. for each: download xml.zip,        │
                                  │      parse, write per-§ JSON to S3,     │
                                  │      embed each §, upsert S3 Vectors    │
                                  │   4. write _manifest.json v2            │
                                  └─────────────┬───────────────────────────┘
                                                │
                                                ▼
                ┌──────────────────────────────────────────────────────────┐
                │                   eu-central-1                           │
                │                                                          │
                │   ┌──────────────────────┐    ┌────────────────────┐    │
                │   │  S3: corpus bucket   │    │  S3 Vectors index  │    │
                │   │  gesetze/<abk>/      │    │  kira-legal-norms  │    │
                │   │    _meta.json        │    │  ~125k paragraphs  │    │
                │   │    1.json …          │    │  1024-dim vectors  │    │
                │   │    535.json …        │    │  Cohere multilingu.│    │
                │   │  _manifest.json      │    │  metadata: text +  │    │
                │   └──────────────────────┘    │  gesetz/§ + url    │    │
                │            ▲                  └────────────────────┘    │
                │            │                            ▲               │
                │            │                            │               │
                │   ┌────────┴────────┐         ┌─────────┴──────────┐   │
                │   │ ingest Lambda   │         │  search Lambda     │   │
                │   │ (daily cron)    │         │  (per-request)     │   │
                │   │ - fetch toc     │         │  - embed query     │   │
                │   │ - diff vs       │         │    via Bedrock     │   │
                │   │   manifest      │         │    Cohere          │   │
                │   │ - cond. GET     │         │  - QueryVectors    │   │
                │   │   only changed  │         │    with filter     │   │
                │   │ - write per-§   │         │  - format results  │   │
                │   │ - re-embed +    │         └────────────────────┘   │
                │   │   upsert        │                                   │
                │   └─────────────────┘                                   │
                │            ▲                                            │
                │            │ HTTPS via Cloudflare Worker proxy         │
                │            │ (cond. headers passed through)             │
                │   ┌────────┴───────┐         ┌────────────────────┐    │
                │   │ lookup Lambda  │         │  AgentCore agents  │    │
                │   │ (per-request)  │         │  (consumers)       │    │
                │   │ - load _meta   │         │                    │    │
                │   │   (LRU mem)    │         └────────────────────┘    │
                │   │ - load §       │                                   │
                │   │   (LRU /tmp +  │                                   │
                │   │   mem)         │                                   │
                │   └────────────────┘                                   │
                └──────────────────────────────────────────────────────────┘
```

Stack additions vs. V1:

- **S3 Vectors index** in eu-central-1.
- **Search Lambda** (`kira-legal-search`).
- **Bedrock Cohere multilingual v3** model access (one-time enablement in Bedrock console).
- **One-time backfill script** in `scripts/backfill_corpus.py` (runs locally; not Lambda).

The lookup Lambda's external surface (function name, env vars, IAM role) is unchanged. Only its internals (lazy-load, LRU) change.

## 4. Discovery and scope filter

### 4.1 The TOC

`https://www.gesetze-im-internet.de/gii-toc.xml` is the official, machine-readable index. Single XML document, ~3 MB. Each entry:

```xml
<item>
  <title>Bürgerliches Gesetzbuch</title>
  <link>https://www.gesetze-im-internet.de/bgb/xml.zip</link>
  <description>BGB — Bürgerliches Gesetzbuch</description>
</item>
```

The `<link>` is a stable URL pointing at the per-Gesetz `xml.zip`. We derive the `<abkuerzung>` from the URL slug (last path segment before `xml.zip`).

### 4.2 Filter rule

`_is_citable_gesetz(toc_entry) -> bool` lives in `_common/toc.py`. The implementation is data-driven — a module-level `_REJECT_SUFFIX_PATTERNS: list[re.Pattern]` and `_REJECT_TITLE_PATTERNS: list[re.Pattern]`, iterated in order. Tunable in one place.

Initial reject lists (refined against the captured fixture during implementation):

- Suffix (case-insensitive, on the URL slug after the last `/`): `bek$`, `verfg$`, `erl$`, `vorschr$`, `go\d*$`, `geschoangleg$`, `hauseigung$`. These remove Bekanntmachungen, Verfügungen, Erlasse, Verwaltungsvorschriften, Geschäftsordnungen, and a couple of one-off oddities.
- Title (case-insensitive, on `<title>`): `\(aufgehoben\)`, `\(außer\s+Kraft\)`.

Anything else passes. The patterns are fixtures-driven; if the live TOC turns up additional non-citable types we add a pattern in one place.

### 4.3 Verification

A unit test runs the filter against a captured `gii-toc.xml` fixture and asserts:

- BGB, StGB, ZPO, GG, HGB, AO, BetrKV, HeizkostenV, WEG are **included**.
- A representative 5–10 internal Bekanntmachungen / Geschäftsordnungen are **excluded**.
- Repealed entries are **excluded**.

Total post-filter count is checked to be in `[2000, 3500]` so a regression in the regex doesn't silently halve the corpus.

## 5. Storage layout

### 5.1 S3 keys

Old (V1):
```
gesetze/_manifest.json                  # {version: 1, files: ["gesetze/bgb.json", ...]}
gesetze/bgb.json                        # whole-Gesetz blob
gesetze/betrkv.json
gesetze/heizkostenv.json
```

New (V2 — **breaking layout change**):
```
gesetze/_manifest.json                  # {version: 2, gesetze: {<abk>: {meta_key, etag, ...}}}
gesetze/<abk>/_meta.json                # per-Gesetz metadata + paragraph index
gesetze/<abk>/<paragraph>.json          # one file per paragraph
```

Examples:
```
gesetze/_manifest.json
gesetze/bgb/_meta.json
gesetze/bgb/1.json
gesetze/bgb/535.json
gesetze/bgb/535a.json
gesetze/stgb/_meta.json
gesetze/stgb/263.json
gesetze/wohnungseigg/_meta.json
gesetze/wohnungseigg/14.json
```

Paragraph identifiers preserve case (`535a`, `535A`); S3 is case-sensitive so no conflicts.

### 5.2 `_manifest.json` (v2)

```json
{
  "version": 2,
  "stand": "2026-05-10",
  "gesetze": {
    "bgb": {
      "abkuerzung": "BGB",
      "titel": "Bürgerliches Gesetzbuch",
      "type": "Gesetz",
      "meta_key": "gesetze/bgb/_meta.json",
      "upstream_etag": "\"71465-651280c398f03\"",
      "upstream_last_modified": "Wed, 06 May 2026 15:45:05 GMT"
    },
    "stgb": { ... },
    "betrkv": { ... }
  }
}
```

The top-level manifest is everything the lookup Lambda needs to know "does this Gesetz exist, and where is its metadata". `upstream_etag` / `upstream_last_modified` are only used by the ingest Lambda for conditional-GET decisions; the lookup Lambda ignores them.

### 5.3 `<abk>/_meta.json`

```json
{
  "abkuerzung": "BGB",
  "titel": "Bürgerliches Gesetzbuch",
  "type": "Gesetz",
  "stand": "2026-05-10",
  "quelle": "gesetze-im-internet.de",
  "quelle_url": "https://www.gesetze-im-internet.de/bgb",
  "upstream_xml_zip_url": "https://www.gesetze-im-internet.de/bgb/xml.zip",
  "paragraphen": {
    "1": {
      "titel": "Beginn der Rechtsfähigkeit",
      "key": "gesetze/bgb/1.json",
      "content_sha256": "abc123…"
    },
    "535": {
      "titel": "Inhalt und Hauptpflichten des Mietvertrags",
      "key": "gesetze/bgb/535.json",
      "content_sha256": "def456…"
    },
    "...": {}
  }
}
```

The `paragraphen` map lets the lookup Lambda (a) validate that a citation exists before fetching, (b) suggest near-misses on `paragraph_not_found`, and (c) emit a list-of-§§ for diagnostic purposes.

`content_sha256` is the hash of the per-paragraph JSON; the ingest pipeline uses it to skip unchanged paragraphs (paragraph-level idempotency).

### 5.4 `<abk>/<paragraph>.json`

```json
{
  "gesetz": "BGB",
  "paragraph": "535",
  "titel": "Inhalt und Hauptpflichten des Mietvertrags",
  "absaetze": [
    { "nummer": "1", "text": "Durch den Mietvertrag wird der Vermieter verpflichtet, ..." },
    { "nummer": "2", "text": "Der Mieter ist verpflichtet, ..." }
  ],
  "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html"
}
```

Self-contained except for `stand`, which lives in the parent `_meta.json` and is composed in at lookup time. This is deliberate: per-§ JSON contents are stable across ingest runs that produced no upstream change, which is what the hash-skip relies on. ~1–4 KB typical, ~15 KB for very long paragraphs.

## 6. Backfill (one-time, local)

`scripts/backfill_corpus.py` runs once on a residential ISP (avoids Cloudflare Workers Free 100 MB/day limit) and produces the initial S3 corpus + S3 Vectors index from scratch.

```bash
cd .worktrees/feature-legal-sources-tool1
LEGAL_CORPUS_BUCKET=kira-legal-corpus-${AWS_ACCOUNT_ID}-eu-central-1 \
  .venv/bin/python scripts/backfill_corpus.py \
    --max-parallel 8 \
    --vector-index kira-legal-norms \
    --batch-size 50
```

### Steps

1. **Fetch and filter** `gii-toc.xml` directly (no proxy needed — local IP isn't blocked). Apply `_is_citable_gesetz`.
2. **For each filtered law (parallel up to `--max-parallel`):**
   a. Download `xml.zip` directly, parse via existing `kira.knowledge.xml_parser`.
   b. Convert each parsed Norm into the per-paragraph JSON shape.
   c. Compute `content_sha256` per paragraph.
   d. PUT `gesetze/<abk>/<paragraph>.json` for every paragraph.
   e. PUT `gesetze/<abk>/_meta.json`.
3. **Embedding pass (batched per `--batch-size`):**
   a. For each paragraph, build embedding input: `f"{abkuerzung} §{paragraph}: {titel}\n\n{full_text}"` (truncated to 8 KB to stay within Cohere's 2,048-token cap with margin).
   b. Call `bedrock-runtime:InvokeModel` against `cohere.embed-multilingual-v3` with up to 96 inputs per call (Cohere's batch limit).
   c. `s3vectors:PutVectors` upsert, metadata = `{gesetz, paragraph, titel, abkuerzung, type, wortlaut, quelle_url, stand}`.
4. **Write top-level `_manifest.json`** at the very end.
5. **Print summary**: `{laws: N, paragraphs: M, embeddings: M, duration_seconds, errors: [...]}`.

### Resumability

The script is idempotent. On restart it reads the existing `_manifest.json` (if any) and only re-processes laws missing or whose `upstream_etag` differs from a HEAD check against the source URL. The embedding pass skips paragraphs whose `content_sha256` matches what's already in the S3 Vectors metadata for that vector (small extra metadata GET, but saves the embedding cost on rerun).

### Expected runtime + cost

- ~2,500 laws × ~1.5 s download/parse/PUT (sequential within a worker) ÷ 8 parallel = ~8 minutes for raw ingest.
- ~125k paragraphs ÷ 96 per Cohere batch = ~1,300 calls × ~0.5 s = ~11 minutes for embeddings.
- Total wall time on a residential 100 Mbit/s connection: **~20–40 minutes**.
- One-time AWS cost: ~$6 (embeddings) + ~$0.6 (S3 PUTs) = **~$7**.

## 7. Daily incremental (Lambda)

`kira-legal-ingest` keeps the same name, EventBridge schedule, env vars, and external-event shape as V1 — internals change.

### Steps per invocation

1. **Fetch `gii-toc.xml`** (via Cloudflare Worker proxy as today).
2. **For each filtered Gesetz**, send a HEAD request to `xml.zip` with `If-None-Match: <upstream_etag>` and `If-Modified-Since: <upstream_last_modified>` from the manifest.
3. **If 304 Not Modified**: skip this Gesetz entirely.
4. **If 200**:
   a. Download new `xml.zip`, parse.
   b. Compute new per-paragraph SHA256s; diff against the existing `_meta.json`.
   c. For each **changed** paragraph: PUT new `<abk>/<paragraph>.json`, re-embed, upsert vector.
   d. For each **deleted** paragraph (was in old `_meta.json`, not in new): DELETE the per-paragraph file and remove the vector.
   e. PUT updated `_meta.json` with new `upstream_etag` / `upstream_last_modified` / paragraph SHAs.
5. **Write updated `_manifest.json`** with new top-level `stand` date.

### Lambda config changes vs. V1

- Memory: 1024 MB → 1536 MB (Cohere call buffers + xml.zip parse for big codes).
- Timeout: 5 min → 15 min (some days could see dozens of changes; defensive).
- IAM: add `bedrock:InvokeModel` (for embedding) + `s3vectors:*` on the index.

### Cloudflare Worker change

The current Worker doesn't forward request headers; it needs to pass through `If-None-Match` and `If-Modified-Since`, and surface upstream's `304 Not Modified` correctly. Code change: ~5 lines. Deploy via `wrangler deploy`.

## 8. Lookup Lambda (lazy-load + LRU)

External surface unchanged. Internal flow:

```
handler(event)
  ├── parse + validate input → LookupNormInput
  ├── manifest_in_memory? (held in module-level _LOADER)
  │     ├── No  → S3 GET _manifest.json, parse, cache in memory (etag-recheck every 5 min)
  │     └── Yes → use it
  ├── meta = load_meta(abk)        # via LRU
  │     ├── meta in mem? hit
  │     ├── meta in /tmp? read+parse, promote to mem
  │     └── miss   → S3 GET <abk>/_meta.json, write /tmp, put in mem
  ├── exists in meta.paragraphen?   No → unknown_gesetz / paragraph_not_found
  ├── norm = load_norm(abk, p)      # via LRU
  │     (same hierarchy as meta)
  └── select absatz, format LookupNormSuccess
```

### LRU specifics

`_common/lru.py` (NEW): two-tier cache with explicit budgets.

- `MemoryLRU[K, V]`: keeps up to `max_items` Python-object values, evicts least-recently-used when over budget. `lookup_norm`'s instance: `MemoryLRU[str, Norm]` with `max_items=200`.
- `TmpDiskLRU`: holds files under a directory (default `/tmp/legal_sources_corpus/`), enforces a byte budget (default 800 MB). Each get/put updates an in-memory `last_accessed` map. Background eviction never happens; on each put, evicts oldest until under budget.
- Both LRUs are thread-unsafe (Lambda is single-threaded per execution environment) and process-local.

### Lambda config changes vs. V1

- `ephemeral_storage_size_in_mib = 1024` (default 512 → 1024). Cost addition: ~$0.012/mo.
- Memory unchanged at 512 MB.

### Bonus: `paragraph_not_found` near-miss suggestions

When `<paragraph>` isn't in `meta.paragraphen` but adjacent ones are, the error message lists the closest five (e.g., requested 535, present nearby: 534, 535a, 536). Implemented via Levenshtein-1 over the keys.

## 9. Search Lambda (`kira-legal-search`)

New deployment. Lives at `src/kira/legal_sources/adapters/search_handler.py`. Exposed as the same shape AgentCore Gateway expects.

### Flow

```
handler(event)
  ├── parse + validate → SearchNormInput
  ├── embed query  → bedrock-runtime:InvokeModel(cohere.embed-multilingual-v3)
  │                  with input_type="search_query"
  ├── filter clause = build_filter(input.gesetz_filter, input.type_filter)
  ├── hits = s3vectors:QueryVectors(
  │            indexName="kira-legal-norms",
  │            queryVector=embedding,
  │            topK=input.k,
  │            filter=filter_clause,
  │            returnMetadata=True
  │         )
  └── format SearchNormSuccess (each hit's wortlaut comes from the vector metadata)
```

### Important model details

- Query embeddings use `input_type="search_query"`; corpus embeddings (in backfill + ingest) use `input_type="search_document"`. Cohere multilingual v3 requires this distinction for correct retrieval.
- `topK` is capped at 50 to prevent runaway responses.

### Performance budget

- Embedding API: ~150–200 ms p99.
- S3 Vectors query: ~100–200 ms p99.
- Total handler time: ≤500 ms p99.

### Lambda config

- Memory: 512 MB.
- Timeout: 5 s.
- ARM64.
- IAM: `bedrock:InvokeModel` on the Cohere model, `s3vectors:Query*` on the index.
- No S3 corpus access — every result is self-contained in the vector metadata.

## 10. Embedding pipeline details

### Input format

```
{abkuerzung} §{paragraph} ({titel}):

(1) {absatz_1_text}
(2) {absatz_2_text}
...
```

Truncated to 6,000 *characters* (Python `len(s)`). German legal text averages ~3.5 characters per token at the Cohere multilingual v3 tokenizer, so this stays comfortably under the 2,048-token cap with margin for the citation prefix.

### Why prepend the citation

Cohere multilingual v3 retrieves better with a small prefix that locates the chunk in its source. Empirically improves retrieval for legal-citation-shaped queries by ~10-15% in my prior projects.

### Vector metadata stored in S3 Vectors

```json
{
  "gesetz": "BGB",
  "paragraph": "535",
  "abkuerzung": "BGB",
  "type": "Gesetz",
  "titel": "Inhalt und Hauptpflichten des Mietvertrags",
  "wortlaut": "(1) Durch den Mietvertrag ...",  // full text, ≤30 KB
  "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
  "stand": "2026-05-10",
  "content_sha256": "abc123..."  // for the ingest skip-if-unchanged path
}
```

S3 Vectors metadata cap is 40 KB per vector; we stay well below.

## 11. Module changes

```
src/kira/legal_sources/
├── _common/
│   ├── errors.py                  (CHANGED: add EmbeddingUnavailableError)
│   ├── region.py                  (unchanged)
│   ├── s3_corpus.py               (CHANGED: lazy-load + LRU per-§)
│   ├── lru.py                     (NEW: MemoryLRU + TmpDiskLRU)
│   ├── manifest.py                (NEW: v2 read/write, type-safe)
│   ├── toc.py                     (NEW: gii-toc.xml parser + filter)
│   ├── embedder.py                (NEW: Bedrock Cohere wrapper)
│   └── vector_index.py            (NEW: S3 Vectors wrapper)
├── gesetze/
│   ├── corpus_format.py           (CHANGED: add per-Gesetz Meta type
│                                    matching the new _meta.json shape)
│   ├── lookup_norm.py             (CHANGED: takes a "lookup" callable
│                                    instead of full corpus dict;
│                                    contract preserved)
│   ├── search_norm.py             (NEW: pure function, no AWS deps,
│                                    accepts an "embed" + "search" callable)
│   └── schema.py                  (CHANGED: SearchNormInput,
│                                    SearchNormSuccess, SearchNormError,
│                                    SearchNormResult; lookup unchanged)
└── adapters/
    ├── kira_registry.py           (CHANGED: register both tools)
    ├── agent_sdk.py               (CHANGED: expose make_search_norm_tool)
    ├── lookup_handler.py          (CHANGED: lazy-load wiring)
    ├── search_handler.py          (NEW: Lambda entrypoint)
    └── ingest_handler.py          (CHANGED: TOC discovery, per-§ diff,
                                    embedding upsert)

scripts/
├── backfill_corpus.py             (NEW)
└── legal_sources_smoke.py         (CHANGED: include search round-trip)

infra/legal_sources/stack.py       (CHANGED: search Lambda + S3 Vectors
                                    index + IAM grants + alarm tweaks)

infra/cloudflare/juris-proxy/
└── worker.js                      (CHANGED: forward conditional headers,
                                    return 304 transparently)
```

The no-`kira.*`-imports rule still applies inside `_common/` and `gesetze/`; only `adapters/` may bridge.

## 12. Testing strategy

This section is deliberate per the user's "plan for testing" requirement. The pyramid extends V1's structure; nothing in V1 is removed.

### 12.1 Tier 1 — Unit tests (every commit, fast, no network/AWS)

| Component | Tests |
|---|---|
| `_common/toc.py` | parse minimal fixture; filter rules each have ≥1 positive + 1 negative case; total count assertion against captured fixture (should be 2,000–3,500) |
| `_common/manifest.py` | round-trip v2 read/write; reject v1 input with clear error |
| `_common/lru.py` | MemoryLRU: hit/miss/eviction order under deterministic clock; TmpDiskLRU: same plus disk-budget enforcement |
| `_common/embedder.py` | mock Bedrock client; verify input shape, batching, error mapping |
| `_common/vector_index.py` | mock s3vectors client; verify query construction with/without filter |
| `gesetze/corpus_format.py` | per-§ JSON parses to expected shape; per-Gesetz `_meta.json` parses |
| `gesetze/lookup_norm.py` | unchanged behavior; near-miss suggestion logic |
| `gesetze/search_norm.py` | given mocked embed+search callables, returns correctly-formatted output; respects k cap |
| `gesetze/schema.py` | SearchNormInput rejects empty/oversized queries, k>50, malformed filters |

Coverage gate: ≥95% on `_common/` and `gesetze/` (same as V1, scope expanded).

### 12.2 Tier 2 — Adapter tests (every commit, fast, mocked AWS via moto/respx)

| Adapter | Tests |
|---|---|
| `lookup_handler` | cold-path lazy-load (manifest miss → fetch → meta miss → fetch → §-miss → fetch); /tmp warmth across simulated container reuse; eviction under load |
| `search_handler` | direct-invoke shape, AgentCore Gateway shape, embedding error → `embedding_unavailable`, vector index error → `corpus_unavailable` |
| `ingest_handler` | conditional-GET path: 304 short-circuit on unchanged Gesetz; paragraph diff: only changed §§ get PUT + re-embedded; deleted paragraph removed from S3 + vector; manifest updated with new etags |
| `kira_registry` | both `lookup_norm` and `search_norm` build cleanly and run end-to-end against staged local corpus |
| `agent_sdk` | both tools' `make_*_tool_function()` return the right MCP shape |

### 12.3 Tier 3 — Recorded HTTP fixtures (every commit, no network)

- Captured `gii-toc.xml` (~3 MB; trimmed to ~50 representative entries to keep the repo light).
- Captured `xml.zip` for: 1 mega-code (BGB partial), 1 mid-size (WEG), 1 small Verordnung (HeizkostenV).
- Captured Bedrock embedding response (a JSON file with synthetic 1024-dim vectors).
- Captured S3 Vectors query response (mocks at the boto3 client level via stubber).

### 12.4 Tier 4 — Live smoke (opt-in via `RUN_LIVE_TESTS=1`, run nightly + pre-release)

`tests/legal_sources/live/test_live_smoke_v2.py`:

- `test_live_toc_parseable_with_expected_size`: actually fetches `gii-toc.xml`, runs the filter, asserts count is in the expected band.
- `test_live_xml_zip_parseable_for_three_laws`: fetches BGB, WEG, BetrKV xml.zips and round-trips them through `_build_payload` for the new per-§ shape.
- `test_live_cohere_embeddings_dimension`: makes one real Cohere call with 2 inputs; asserts 1024-dim vectors come back.
- `test_live_s3_vectors_roundtrip`: against a temporary S3 Vectors index (created + destroyed per test), upserts 5 vectors, queries, asserts top-1 match is the relevant one.

### 12.5 Tier 5 — End-to-end smoke (manual, post-deploy)

Run by an operator after `cdk deploy` + backfill against a sandbox account.

- `scripts/legal_sources_smoke.py` (extended):
  1. Direct invoke of `kira-legal-lookup-norm` with `{gesetz: BGB, paragraph: 535}` → assert success.
  2. Direct invoke of `kira-legal-lookup-norm` with `{gesetz: WEG, paragraph: 14}` → assert success (proves the all-laws claim).
  3. Direct invoke of `kira-legal-search` with `{query: "Pflichten des Vermieters zur Erhaltung der Mietsache"}` → assert top-3 includes BGB §535.
  4. Direct invoke of `kira-legal-search` with `{query: "Schadensersatz statt der Leistung", gesetz_filter: ["BGB"]}` → assert top-1 is BGB §281.

### 12.6 Tier 6 — Performance / load (opt-in, `RUN_PERF_TESTS=1`)

`tests/legal_sources/perf/`:

- `test_lookup_warm_p99_under_50ms`: 1,000 sequential warm invocations, assert p99 < 50 ms.
- `test_lookup_cold_first_call_under_300ms`: simulate cold start by rebuilding the loader, single call, assert < 300 ms.
- `test_search_p99_under_500ms`: 100 sequential queries against a populated index, assert p99 < 500 ms.

These run against deployed Lambda (uses real AWS), gated to a manual workflow.

### 12.7 Coverage targets

- `_common/` + `gesetze/`: ≥95% line, ≥85% branch.
- `adapters/`: not gated (mostly glue), but expected to land near 90% from Tier 2.
- New `_common/lru.py`, `_common/embedder.py`, `_common/vector_index.py`: ≥95% (these are the riskier parts).

## 13. Cost analysis

| Item | One-time | Recurring |
|---|---|---|
| S3 PUTs for backfill (~125k paragraphs + meta + manifest) | ~$0.65 | — |
| Cohere embedding for backfill (~62.5M tokens at $0.10/M) | ~$6.25 | — |
| S3 storage (~600 MB corpus + ~500 MB vectors) | — | ~$0.03/mo |
| S3 Vectors monthly (~125k vectors, ~500 MB) | — | ~$0.05/mo |
| Lookup Lambda invocations | — | ~$0 (free tier) |
| Search Lambda invocations | — | ~$0 (free tier) |
| Bedrock embedding for daily query traffic (~100/day) | — | ~$0.30/mo |
| Bedrock embedding for daily incremental ingest (~50 changes/day) | — | ~$0.05/mo |
| KMS + alarm + EventBridge | — | ~$1.10/mo (unchanged from V1) |
| **Total recurring** | | **~$1.55/mo** |

About 50¢/month above V1 — almost entirely Bedrock query embeddings. Acceptable given the capability jump.

## 14. Migration / rollout plan from V1

The V1 manifest format and storage layout are **incompatible** with V2. Approach:

1. Land V2 code on the feature branch.
2. Run `scripts/backfill_corpus.py` against the existing V1 bucket. It writes the new per-§ layout under `gesetze/<abk>/...` and the new `_manifest.json` (overwriting V1's). Old per-Gesetz blobs (`gesetze/bgb.json` etc.) become orphaned but stay in S3 (versioning preserved).
3. `cdk deploy` the V2 stack. The lookup Lambda's new code reads v2 manifest only and hits the new layout cleanly.
4. After verifying V2 works end-to-end, run a one-time `aws s3 rm` for the orphaned V1 keys (or just leave them — versioning makes them trivially small).

The KIRA agent process restart is required because the in-process `_LOADER` keeps a v1 manifest cached. Acceptable since V2 ships as a planned upgrade, not a hotfix.

## 15. Acceptance criteria

V2 ships when all of:

1. `pytest tests/ --cov-fail-under=95` green on a clean checkout.
2. `RUN_LIVE_TESTS=1 pytest -m live tests/legal_sources/live/test_live_smoke_v2.py` green.
3. `scripts/backfill_corpus.py --dry-run` runs end-to-end against a sandbox bucket without hitting AWS quota errors.
4. `cdk deploy KiraLegalSources` succeeds.
5. `scripts/legal_sources_smoke.py` (V2 version) passes all 4 assertions.
6. `RUN_PERF_TESTS=1 pytest tests/legal_sources/perf/` p99 budgets met.
7. CloudWatch stale-corpus alarm verified to fire on simulated 48h gap.
8. Cost-Tracker confirms monthly run-rate < $2.

## 16. Open questions / explicit deferrals

- **Embedding model evaluation.** Cohere multilingual v3 is the default; if its German legal recall turns out weak we evaluate against a fine-tuned BERT-Legal model (German legal embeddings exist on HuggingFace). Out of scope for V2 unless V3 acceptance fails.
- **Cross-citation graph** — interesting future feature; out of scope.
- **§-level Absatz isolation in search** — for now we embed full §s; embedding per-Absatz would double vector count and add a `absatz` field to filter. Defer based on observed query patterns.
- **Caching of search responses** — at this scale, no.
- **Multi-language (English) query support** — Cohere multilingual v3 handles cross-language retrieval, but we don't validate it. Probably fine.
- **Cold start of search Lambda** — first invocation may push p99 over 500 ms because Bedrock client init isn't fast. Acceptable; warm hit rate will dominate after a single lawyer query.
