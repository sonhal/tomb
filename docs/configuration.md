# Configuration

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
