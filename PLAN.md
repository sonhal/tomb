# tome — Phase 1 plan

## Status

Phase 1 is done: steps 1 to 5 below are implemented. `docs/` documents the
CLI, MCP tools, configuration and sources; `deploy/` has the VPS setup.
Open items: no authentication on `/mcp` yet (keep it on loopback, a private
network, or behind Caddy basic auth), and Phase 2 (embeddings, RRF) has not started.

A Markdown knowledge bank for LLM agents, after Karpathy's "LLM Wiki" pattern:
**capture** sources into an immutable `raw/`, let the agent **integrate** them
into `wiki/`, commit every change, search it all through an MCP server.

Phase 1 is keyword search (SQLite FTS5). Phase 2 adds local embeddings and
hybrid ranking (RRF) without changing the rest.

## Layout

Code (this repo) and the bank (a separate git repo, `TOME_HOME`) are apart, so
the bank's history is only knowledge changes.

```
<bank>/
  TOME.md                              conventions; served as MCP instructions
  inbox/                               drop files here; captured, then removed
  raw/<source>/<yyyy>/<mm>/<slug>.md   immutable: frontmatter + converted text
  wiki/index.md                        catalogue, maintained by the agent
  wiki/log.md                          chronological log (capture + integrate)
  wiki/**/*.md                         pages with [[wikilinks]] and [@raw/…md#cN] citations
  .tome/index.db                       SQLite, gitignored, rebuildable
```

## Capture → integrate

```
 capture (mechanical, many adapters)            integrate (agent)
 inbox/ drop folder  ─┐                          lint → uncited-sources queue
 MCP ingest_text     ─┼─▶ raw/ ──commit──▶        read → update wiki/, index.md,
 nyttig connector    ─┤                           log.md, cite chunks ──commit──▶
 future connectors   ─┘
```

Every connector implements `fetch(**params) -> Iterable[Source]` and ends in the
same `add_sources()`: dedupe on `key`, write raw files (exclusive create,
chmod 0444), append to `wiki/log.md`, one commit per batch, index.

## Modules (`src/tome/`)

| Module | Job |
|---|---|
| `bank.py` | paths, `init`, write-path guard (wiki only), raw create-only writes, write lock, git commit of touched paths only |
| `markdown.py` | frontmatter, slugs, wikilinks/citations, heading-aware chunker (~350 words, ~50 overlap, ids `<path>#cN`) |
| `index.py` | SQLite schema, incremental sync (mtime → sha), FTS5 search (exact AND → prefix AND → prefix OR, BM25 title 5/heading 2/body 1), listings, backlinks |
| `ingest.py` | `Source`, `add_sources()`, HTML→MD (trafilatura), PDF→text (pypdf) |
| `connectors/` | `inbox`, `nyttig` (items with archives, digests) |
| `lint.py` | raw tampering (git status), broken links/citations, orphans, uncited wiki pages, uncited raw sources |
| `server.py` | MCP over Streamable HTTP: search, read, list_documents, backlinks, write/delete_wiki_page, ingest_text, ingest_nyttig, lint |
| `cli.py` | init, reindex, search, ingest {inbox,nyttig,nyttig-digests,file}, lint, serve |

No URL fetching in the server (SSRF on a networked service); nyttig already
fetches safely. Raw content is untrusted data, never instructions.

## Order of work

1. (done) `bank.py`, `markdown.py`, `index.py` + tests
2. (done) `ingest.py`, inbox connector, `lint.py` + tests
3. (done) `server.py`, `cli.py`, end-to-end check with an MCP client
4. (done) nyttig connector, tested against a mocked transport (live instance: not yet)
5. (done) README: VPS deploy (systemd unit, ingest timer, Caddy)
