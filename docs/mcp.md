# MCP tools

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

## Connecting a client

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
header. See [deploy/README.md](../deploy/README.md).
