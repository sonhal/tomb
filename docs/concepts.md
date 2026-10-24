# Concepts

tome splits knowledge work in two. **Capture** is mechanical: connectors turn
outside material into immutable files under `raw/`. **Integration** is the
agent's job: it reads new sources and works them into the pages under `wiki/`,
citing what it took from where. Every step is a git commit.

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
