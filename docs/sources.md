# Sources

Every capture writes `raw/<source>/<yyyy>/<mm>/<slug>.md`. The year and month
come from the published date, or from the capture time when there is none.
Each file has frontmatter: `title`, `source`, `url`, `author`, `date`, `tags`,
`key`, `ingested` and `content_sha256`, plus connector fields. The key is a
stable id. A source whose key is already captured is skipped as `duplicate`,
and one with an empty body as `empty`. Both appear in the command output.

## inbox

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

## ingest_text

The `ingest_text` tool captures a string as `source: manual`. It takes a title
and content (at most 5 MB), and optionally `url`, `author`, `date` (ISO) and
`tags`. The dedupe key is the SHA-256 of the content.

## nyttig

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

## Writing a connector

A connector is a class with a `name` and a `fetch(**params)` method that yields
`Source` objects. See `src/tome/connectors/__init__.py`. It then calls
`ingest.add_sources(bank, index, sources, label=...)`, which is the only place
raw files are written. That gives it dedupe on `key`, the create-only write,
the log entry, one commit per batch and the index update, with no extra code.
