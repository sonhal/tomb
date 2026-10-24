# tome

A Markdown knowledge bank for LLM agents, after Andrej Karpathy's "LLM Wiki"
idea. Sources are captured once into an immutable `raw/` folder. An agent
works them into a `wiki/` it maintains, and every change is a git commit you
can review with `git diff`. Agents reach the bank through an MCP server over
Streamable HTTP, with SQLite full-text search over both folders.

```
 inbox/       ─┐
 ingest_text  ─┼─▶  raw/  ──▶  agent integrates  ──▶  wiki/
 nyttig       ─┘   (immutable)                      (each change a git commit)
```

## Quick start

Requires Python 3.12+.

```bash
python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/tome init ~/tome-bank
export TOME_HOME=~/tome-bank

.venv/bin/tome ingest file notes.md     # capture a source
.venv/bin/tome search "write-ahead log" # search it
.venv/bin/tome serve                    # MCP at http://127.0.0.1:8765/mcp
```

Connect an agent, for example Claude Code:

```bash
claude mcp add --transport http tome http://127.0.0.1:8765/mcp
```

The server hands the bank's `TOME.md` to the agent as instructions, so it knows
the rules: never touch `raw/`, cite sources as `[@raw/…md#c3]`, keep
`wiki/index.md` and `wiki/log.md` up to date.

## Documentation

| | |
|---|---|
| [Concepts](docs/concepts.md) | Capture and integration, the bank layout, citations, links and lint |
| [Configuration](docs/configuration.md) | Environment variables |
| [CLI reference](docs/cli.md) | Every `tome` command and flag |
| [MCP tools](docs/mcp.md) | The tools agents get, and how to connect a client |
| [Search](docs/search.md) | How queries are matched and ranked, filters |
| [Sources](docs/sources.md) | inbox, `ingest_text`, nyttig, writing a connector |
| [Security](docs/security.md) | Untrusted content, network exposure, no auth yet |
| [Deploying](deploy/README.md) | A VPS with systemd, an ingest timer and Caddy |

## Status

Phase 1 (keyword search with SQLite FTS5) is done. Phase 2 adds local
embeddings and hybrid ranking. See [PLAN.md](PLAN.md).

**There is no authentication yet.** Anything that reaches `/mcp` can write the
wiki. Keep the server on loopback, a private network, or behind a proxy with
auth. See [Security](docs/security.md).
