# CLI reference

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
| `tome search QUERY... [flags]` | Keyword search. See [Search](search.md) |
| `tome ingest inbox [--min-age SECONDS]` | Capture `inbox/`. Default minimum age 10 seconds |
| `tome ingest file FILES... [--tag TAG] [--source NAME]` | Capture files directly. Default source `manual` |
| `tome ingest nyttig [flags]` | Capture archived nyttig items. See [nyttig](sources.md#nyttig) |
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
| `--fts` | Treat the query as FTS5 syntax. See [Search](search.md) |
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
