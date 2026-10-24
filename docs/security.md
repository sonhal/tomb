# Security

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
  [deploy/Caddyfile](../deploy/Caddyfile).

Do not bind it to a public address without one of those in front.
