# tome

A Markdown knowledge bank for LLM agents. It follows Andrej Karpathy's
[LLM Wiki](https://x.com/karpathy) idea: sources are captured once into an
immutable `raw/` folder, and an agent integrates them into a `wiki/` it keeps
up to date. Every change is a git commit, so the owner can review what the
agent did. Keyword search runs over both folders with SQLite FTS5, and agents
reach everything through an MCP server over Streamable HTTP.

Phase 1 is keyword search. Phase 2 adds local embeddings and hybrid ranking
(RRF) without changing the rest. See [PLAN.md](PLAN.md).

## Architecture

```
 capture (mechanical)                             integrate (agent, over MCP)
 ┌──────────────────────────┐
 │ inbox/       drop folder │─┐
 │ ingest_text  MCP tool    │─┼──▶  raw/  ──commit──▶  lint → read → wiki/  ──commit──▶
 │ nyttig       connector   │─┘     (immutable)         index.md, log.md,
 └──────────────────────────┘                           [@raw/...#cN] citations
```

Code and bank are separate. The code is this repository. The bank is a git
repository of its own, at `TOME_HOME`. Its history holds only knowledge
changes.

```
<bank>/                                   git repository
  TOME.md                                 conventions; served to agents as MCP instructions
  inbox/                                  drop files here; captured, then removed (gitignored)
  raw/<source>/<yyyy>/<mm>/<slug>.md      captured sources: frontmatter + converted text
  wiki/index.md                           catalogue, maintained by the agent
  wiki/log.md                             chronological log of captures and integrations
  wiki/**/*.md                           pages with [[links]] and [@raw/...#cN] citations
  .tome/index.db                          SQLite search index (gitignored)
```

What to know about the layout:

- **The index is derived.** `.tome/index.db` is rebuilt from the files on
  every change. If it is damaged or stale, `tome reindex --full` deletes it and
  rebuilds it. Never treat it as data.
- **`raw/` is create-only and read-only.** A source is written once, with an
  exclusive create, and its mode is set to `0444`. Nothing overwrites it. Only
  the capture code writes there.
- **`wiki/` is the agent's.** The agent creates, replaces and deletes pages
  through `write_wiki_page` and `delete_wiki_page`. `wiki/index.md` and
  `wiki/log.md` cannot be deleted.
- **Commits touch only the paths tome wrote.** Each commit names its paths, so
  unrelated working-tree changes are never swept in. The author is `tome
  <tome@localhost>`. Set `TOME_GIT_NAME` and `TOME_GIT_EMAIL` to change it.
- **Writes are serialized.** A file lock on `.tome/write.lock` covers the CLI
  and the server together.

## Install

Python 3.12 or newer is required.

```bash
python -m venv .venv && .venv/bin/pip install -e .
# or, with uv:
uv pip install -e .
```

Create a bank, then point tome at it:

```bash
.venv/bin/tome init ~/tome-bank
export TOME_HOME=~/tome-bank
```

`init` creates the folders, `TOME.md`, the two housekeeping pages and an
initial commit. It is safe to run again.

## Configuration

Settings come from environment variables. Command-line flags override them
where a flag exists.

| Variable | Default | Description |
|---|---|---|
| `TOME_HOME` | none | The bank directory. Required unless `--home` is given |
| `TOME_HOST` | `127.0.0.1` | Address `tome serve` binds. Keep it on loopback |
| `TOME_PORT` | `8765` | Port `tome serve` listens on (1 to 65535) |
| `TOME_ALLOWED_HOSTS` | empty | Extra `Host` header values the MCP endpoint accepts, comma-separated. Loopback is always accepted |
| `TOME_ALLOWED_ORIGINS` | empty | Extra `Origin` header values accepted, comma-separated. Loopback is always accepted |
| `TOME_NYTTIG_URL` | unset | nyttig origin, such as `https://nyttig.example.com`. Setting it enables the nyttig tools and `tome ingest nyttig`. Give the origin only: tome adds `/api/...` itself |
| `TOME_NYTTIG_USER` | unset | HTTP basic auth user for nyttig |
| `TOME_NYTTIG_PASSWORD` | unset | HTTP basic auth password for nyttig |
| `TOME_GIT_NAME` | `tome` | Author name on bank commits (read by `bank.py`) |
| `TOME_GIT_EMAIL` | `tome@localhost` | Author email on bank commits (read by `bank.py`) |

## CLI reference

```
tome [--home PATH] <command>
```

`--home` sets the bank directory and defaults to `$TOME_HOME`.

Exit codes: `0` success, `1` `lint` found problems, `2` error (the message goes
to stderr).

| Command | Does |
|---|---|
| `tome init [path]` | Create a bank at `path` (default: `--home`) |
| `tome reindex [--full]` | Sync the index. `--full` deletes the database first |
| `tome search QUERY... [flags]` | Keyword search. See [Search](#search) |
| `tome ingest inbox [--min-age SECONDS]` | Capture `inbox/`. Default minimum age 10 seconds |
| `tome ingest file FILES... [--tag TAG] [--source NAME]` | Capture files directly. Default source `manual` |
| `tome ingest nyttig [flags]` | Capture archived nyttig items. See [nyttig](#nyttig) |
| `tome ingest nyttig-digests SERIES [--limit N] [--json]` | Capture digests from a nyttig series. `SERIES` is an id or `ASSESSOR/NAME` |
| `tome lint [--json] [--limit N]` | Check links, citations and integration state |
| `tome status` | Counts, the 10 most recent commits and the uncited-source count, as JSON |
| `tome serve [--host HOST] [--port PORT]` | Serve the MCP endpoint at `/mcp` |

`tome search` flags:

| Flag | Meaning |
|---|---|
| `--kind {raw,wiki}` | Only sources or only wiki pages |
| `--tag TAG` | Repeatable. A document must have every tag given |
| `--source NAME` | Only documents captured by this source (`inbox`, `manual`, `nyttig`, ...) |
| `--since WHEN` | Documents dated on or after `WHEN`. ISO date, or a window such as `7d` |
| `--until WHEN` | Documents dated on or before `WHEN`. Same forms as `--since` |
| `--prefix PATH` | Only documents whose path starts with `PATH`, for example `wiki/topics/` |
| `--limit N` | Most results (default 10, at most 100) |
| `--fts` | Treat the query as FTS5 syntax. See [Search](#search) |
| `--json` | Print the results as JSON |

`tome ingest nyttig` flags:

| Flag | Meaning |
|---|---|
| `--view NAME_OR_ID` | A nyttig saved view. Passed to nyttig as is |
| `--query TEXT` | Full-text query in nyttig |
| `--source NAME_OR_ID` | A nyttig source. Names are matched case-insensitively |
| `--tag NAME_OR_ID` | A nyttig tag |
| `--assessor NAME_OR_ID` | A nyttig assessor |
| `--since WHEN` | Items published after `WHEN`. A window (`7d`, `12h`, `2w`) or an ISO date |
| `--min-score 0-1` | Only items whose item-level score is at least this |
| `--limit N` | Most items to read (default 50) |
| `--include-unarchived` | Also capture feed descriptions for items without an archive |
| `--json` | Print the result as JSON |

## MCP tools

`tome serve` exposes these tools. The `nyttig` tools appear only when
`TOME_NYTTIG_URL` is set. Tools marked **read-only** carry the
`readOnlyHint` annotation and do not change the bank.

| Tool | Read-only | Description |
|---|---|---|
| `search` | yes | Keyword search over chunks, best match first. `syntax` is `natural` (default) or `fts` |
| `read` | yes | Read a `raw/` or `wiki/` document, paged with `offset` and `max_chars`, or one chunk with `chunk` |
| `list_documents` | yes | List documents newest first, with the same filters as `search`. `uncited=true` gives the integration queue |
| `backlinks` | yes | What links to a path: wiki pages with `[[links]]` and sources they cite |
| `lint` | yes | Problems: raw tampering, broken links and citations, orphan and uncited pages, uncited sources |
| `status` | yes | Document and chunk counts, the 10 most recent commits, and the number of uncited sources |
| `write_wiki_page` | no | Create or replace a wiki page. Returns the commit and warnings for broken links and citations |
| `delete_wiki_page` | no | Delete a wiki page. `wiki/index.md` and `wiki/log.md` cannot be deleted |
| `log` | no | Append one line (at most 500 characters) to `wiki/log.md` |
| `ingest_text` | no | Capture text as a new source (at most 5 MB), under `manual` |
| `ingest_inbox` | no | Capture the files in `inbox/`, then remove the ones captured |
| `ingest_nyttig` | no | Capture archived nyttig items. Same filters as the CLI, plus `include_unarchived` |
| `ingest_nyttig_digests` | no | Capture digests from a nyttig series (`series` is an id or `assessor/name`) |

Each write tool commits and returns the commit sha. The server sends the
contents of `TOME.md` to the client as its instructions, so the conventions
reach the agent without extra setup.

### Connecting a client

Claude Code:

```bash
claude mcp add --transport http tome http://127.0.0.1:8765/mcp
```

Any other MCP client that takes a JSON server list:

```json
{
  "mcpServers": {
    "tome": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

For a remote server behind Caddy, use the HTTPS URL and add basic auth as a
header. See [deploy/README.md](deploy/README.md).

## Search

`search` runs over chunks, not whole documents. Chunks are heading-aware:
about 350 words each, never crossing a heading, with about 50 words of overlap
between neighbours inside a section. Each result has a chunk id such as
`raw/manual/2026/10/some-title.md#c2`, which you cite as `[@raw/manual/2026/10/some-title.md#c2]`.

### Natural mode (default)

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

### FTS5 mode

`--fts` (CLI) or `syntax: "fts"` (MCP) passes the query to SQLite FTS5 as is:
`"exact phrase"`, `OR`, `NEAR(a b, 5)`, `prefix*`. A malformed query is an
error, not an empty result.

### Filters

| Filter | Form |
|---|---|
| `kind` | `raw` or `wiki` |
| `tags` | Documents with all of these tags |
| `source` | A capture source name |
| `since`, `until` | ISO date, or a window back from now: `12h`, `7d`, `2w`, `3m` (30 days), `1y` (365 days) |
| `prefix` | Path prefix, for example `wiki/security/` |

Relative windows work for `--since` and `--until`, and for `--since` on
`ingest nyttig`. Minutes are not supported.

### Phase 2

Phase 2 adds local embeddings and reciprocal rank fusion (RRF) on top of BM25.
The search tool keeps the same interface.

## Citations, links and lint

Wiki pages link to each other with `[[page-name]]`. The target is relative to
`wiki/`, with no `.md`. `[[page|label]]` and `[[page#heading]]` are accepted,
and only the page part is used.

Wiki pages cite sources with `[@raw/...md#cN]` for one chunk, or
`[@raw/...md]` for a whole document. `read` marks each chunk with a
`<!-- chunk cN -->` line, so an agent can see which chunk to cite.

`tome lint` (or the `lint` tool) reports these categories:

| Category | Meaning | Fails `lint` (exit 1) |
|---|---|---|
| `raw_tampering` | `raw/` has uncommitted changes, so something changed a source outside tome | yes |
| `broken_links` | A `[[link]]` names a page that does not exist | yes |
| `broken_citations` | A `[@...]` names a missing document or chunk, or a non-raw document | yes |
| `orphan_pages` | A wiki page that no other wiki page links to | no |
| `uncited_pages` | A wiki page with no citations | no |
| `uncited_sources` | A raw source no wiki page cites yet. This is the integration queue | no |

`lint` lists up to `--limit` items per category (default 50). The counts are
always complete. Exit code 1 only for the first three categories. Orphans
and uncited items are work to do, not failures.

## Sources

Every capture writes `raw/<source>/<yyyy>/<mm>/<slug>.md`. The year and month
come from the published date, or from the capture time when there is none.
Each file has frontmatter: `title`, `source`, `url`, `author`, `date`, `tags`,
`key`, `ingested` and `content_sha256`, plus connector fields. The key is a
stable id. A source whose key is already captured is skipped as `duplicate`,
and one with an empty body as `empty`. Both appear in the command output.

### inbox

Drop files into `<bank>/inbox/`, then run `tome ingest inbox` or wait for the
ingest timer.

- Supported types: `.md`, `.markdown`, `.txt`, `.html`, `.htm`, `.pdf`. HTML
  is converted with trafilatura, PDF text with pypdf. Markdown keeps its
  frontmatter.
- Subfolders are walked. Hidden files and symlinks are ignored.
- A file modified within the last `--min-age` seconds (default 10) is left for
  a later run, so a copy still in progress is not captured.
- A captured file is removed from `inbox/` after the commit. A duplicate is
  removed too, since its content is already in `raw/`.
- Unsupported, unreadable and empty files stay in `inbox/`. The command lists
  them as `left in inbox`.

### ingest_text

The `ingest_text` tool captures a string as `source: manual`. It takes a title
and content (at most 5 MB), and optionally `url`, `author`, `date` (ISO) and
`tags`. The dedupe key is the SHA-256 of the content.

### nyttig

The nyttig connector reads [nyttig](https://github.com/sonhal/nyttig) over
HTTP. It sends GET requests only.

- **Items** are captured from their saved archive. The archive (HTML or PDF) is
  converted to text. Only archives with status `done` or `partial` count.
- An item without a usable archive is skipped and listed under
  `not_archived`. With `--include-unarchived`, its feed description is
  captured instead, with HTML stripped.
- An item already captured is skipped by its key (`nyttig:item:<id>`) without
  any download.
- `--limit` counts the newest matching items, including ones already captured.
  A scheduled run should therefore use `--since` with a generous `--limit`, as
  the timer does (`--since 2d --limit 500`). Otherwise a busy feed can push
  new items out of the window.
- **Digests** (`nyttig-digests`) are captured from their Markdown bodies, tagged
  `digest`. The key includes the digest's `updated_at` (or `created_at`), so a digest edited in
  nyttig is captured again as a new raw document. The old one stays.
- Output reports `known`, `not_archived` and `failed` counts. A failed item
  does not stop the run.

### Writing a connector

A connector is a class with a `name` and a `fetch(**params)` method that yields
`Source` objects. See `src/tome/connectors/__init__.py`. It then calls
`ingest.add_sources(bank, index, sources, label=...)`, which is the only place
raw files are written. That gives it dedupe on `key`, the create-only write,
the log entry, one commit per batch and the index update, with no extra code.

## Security

**Raw content is untrusted.** Captured text comes from the web and from other
people. `TOME.md` tells agents to treat it as data, and the `read` and nyttig
tool descriptions say the same. Prompt injection is a real risk: a
captured page can contain instructions. Review what an agent writes, and
keep the commit history.

**The server does no URL fetching.** `ingest_text` takes text, not a URL to
download. The only outbound requests tome makes are to nyttig, at
`TOME_NYTTIG_URL`. This keeps tome from being used as an SSRF proxy.

**DNS rebinding protection is on.** The `Host` header must be on the allowed
list, and so must any `Origin` header. Loopback names are always allowed.
Add the public host name with `TOME_ALLOWED_HOSTS` and the public origin with
`TOME_ALLOWED_ORIGINS`. A request with another host gets `421`, and a bad
origin gets `403`.

**There is no authentication yet.** Every client that reaches `/mcp` can
write the wiki, delete pages and capture sources. Until auth lands, keep the
server on one of:

- loopback only (the default `127.0.0.1`), for a single machine;
- a private network, such as WireGuard or Tailscale, with no public port;
- behind a reverse proxy that adds basic auth, such as Caddy as in
  [deploy/Caddyfile](deploy/Caddyfile).

Do not bind it to a public address without one of those in front.

## Deploying

[deploy/README.md](deploy/README.md) covers a single VPS: a `tome` service user,
the code in `/opt/tome`, the bank in `/var/lib/tome/bank`, the MCP service and
a 30-minute capture timer under systemd, Caddy with basic auth in front, and
backups by pushing the bank to a private git remote.
