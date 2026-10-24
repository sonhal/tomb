# Search

`search` runs over chunks, not whole documents. Chunks are heading-aware:
about 350 words each, never crossing a heading, with about 50 words of overlap
between neighbours inside a section. Each result has a chunk id such as
`raw/manual/2026/10/some-title.md#c2`, which you cite as `[@raw/manual/2026/10/some-title.md#c2]`.

## Natural mode (default)

The query is split into words. English and Norwegian stopwords are dropped
when other words remain. Then tome tries three steps, and fills the results
from each until it has enough:

1. All the words, each matched exactly, all required.
2. All the words, as prefixes. Words of four characters or more match as prefixes.
3. Any word, as a prefix. Only when there is more than one word.

There is no stemmer, so the search is not tied to one language. Accents are
folded: `café` matches `cafe` and `Åse` matches `Ase`. `ø` and `æ` are not
folded, so `kjøkken` does not match `kjokken`.

Ranking is BM25 with weights title 5, heading 2 and body 1. At most 2 chunks from
the same document appear in one result set.

## FTS5 mode

`--fts` (CLI) or `syntax: "fts"` (MCP) passes the query to SQLite FTS5 as is:
`"exact phrase"`, `OR`, `NEAR(a b, 5)`, `prefix*`. A malformed query is an
error, not an empty result.

## Filters

| Filter | Form |
|---|---|
| `kind` | `raw` or `wiki` |
| `tags` | Documents with all of these tags |
| `source` | A capture source name |
| `since`, `until` | ISO date, or a window back from now: `12h`, `7d`, `2w`, `3m` (30 days), `1y` (365 days) |
| `prefix` | Path prefix, for example `wiki/security/` |

Relative windows work for `--since` and `--until`, and for `--since` on
`ingest nyttig`. Minutes are not supported.

## Phase 2

Phase 2 adds local embeddings and reciprocal rank fusion (RRF) on top of BM25.
The search tool keeps the same interface.
