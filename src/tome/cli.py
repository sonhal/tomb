"""Command line: init, reindex, search, ingest, lint, status and serve.

Exit codes: 0 ok, 1 lint found problems, 2 error (message on stderr).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import lint as linting
from .bank import Bank
from .config import Settings
from .connectors import inbox
from .connectors import nyttig as nyttig_connector
from .connectors.nyttig import NyttigClient
from .index import Index
from .ingest import Source, add_sources, source_from_file
from .server import serve, status_report

LINT_FAILS = ("raw_tampering", "broken_links", "broken_citations")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as e:
        print(f"tome: {e}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tome", description="A Markdown knowledge bank for LLM agents.")
    parser.add_argument("--home", type=Path, help="bank directory (default: $TOME_HOME)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a bank")
    p.add_argument("path", nargs="?", type=Path, help="directory to create (default: --home)")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("reindex", help="rebuild the search index")
    p.add_argument("--full", action="store_true", help="delete the database first")
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("search", help="keyword search")
    p.add_argument("query", nargs="+")
    p.add_argument("--kind", choices=["raw", "wiki"])
    p.add_argument("--tag", action="append", dest="tags", default=[], help="repeatable")
    p.add_argument("--source")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--prefix")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--fts", action="store_true", help="query uses FTS5 syntax")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("ingest", help="capture sources into raw/")
    ingest = p.add_subparsers(dest="ingest_command", required=True)
    q = ingest.add_parser("inbox", help="capture the inbox/ folder")
    q.add_argument("--min-age", type=float, default=10.0, metavar="SECONDS")
    q.set_defaults(func=cmd_ingest_inbox)
    q = ingest.add_parser("file", help="capture files")
    q.add_argument("files", nargs="+", type=Path)
    q.add_argument("--tag", action="append", dest="tags", default=[], help="repeatable")
    q.add_argument("--source", default="manual")
    q.set_defaults(func=cmd_ingest_file)
    q = ingest.add_parser("nyttig", help="capture archived items from nyttig")
    q.add_argument("--view", help="saved view, by name or id")
    q.add_argument("--query", help="full-text query")
    q.add_argument("--source", help="nyttig source, by name or id")
    q.add_argument("--tag", help="tag, by name or id")
    q.add_argument("--since", help="window (7d, 12h, 2w) or ISO date")
    q.add_argument("--assessor", help="assessor, by name or id")
    q.add_argument("--min-score", type=float, metavar="0-1")
    q.add_argument("--limit", type=int, default=50)
    q.add_argument("--include-unarchived", action="store_true", help="capture feed descriptions too")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_ingest_nyttig)
    q = ingest.add_parser("nyttig-digests", help="capture digests from a nyttig series")
    q.add_argument("series", help="series id, or ASSESSOR/NAME")
    q.add_argument("--limit", type=int, default=10)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_ingest_nyttig_digests)

    p = sub.add_parser("lint", help="check links, citations and integration state")
    p.add_argument("--json", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("status", help="counts and recent commits")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("serve", help="serve over MCP (Streamable HTTP)")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)
    return parser


def _bank(args: argparse.Namespace) -> tuple[Bank, Index]:
    bank = Bank(Settings.from_env(home=args.home).home)
    if not bank.is_initialized():
        raise ValueError(f"{bank.root} is not an initialized bank; run `tome init`")
    return bank, Index(bank)


def cmd_init(args: argparse.Namespace) -> int:
    root = Settings.from_env(home=args.path or args.home).home
    print(Bank.init(root).root)
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    bank, _ = _bank(args)
    if args.full:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{bank.db_path}{suffix}").unlink(missing_ok=True)
    index = Index(bank)
    stats = index.sync(force=True)
    print(", ".join(f"{k} {v}" for k, v in stats.as_dict().items()))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    bank, index = _bank(args)
    index.sync(force=True)
    results = index.search(
        " ".join(args.query),
        kind=args.kind, tags=args.tags or None, source=args.source, since=args.since,
        until=args.until, prefix=args.prefix, limit=args.limit,
        syntax="fts" if args.fts else "natural",
    )
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    if not results:
        print("no results")
    for r in results:
        print(f"{r['score']:7.3f}  {r['chunk']}  {r['title']} — {r['heading']}")
        print(f"         {r['snippet']}")
    return 0


def cmd_ingest_inbox(args: argparse.Namespace) -> int:
    bank, index = _bank(args)
    result = inbox.ingest_inbox(bank, index, min_age=args.min_age)
    _print_ingest(result["added"], result["skipped"], result["commit"])
    for rel in result["left"]:
        print(f"left in inbox: {rel}")
    return 0


def cmd_ingest_file(args: argparse.Namespace) -> int:
    bank, index = _bank(args)
    sources: list[Source] = []
    for path in args.files:
        src = source_from_file(path, source=args.source, tags=args.tags)
        if src is None:
            print(f"tome: unsupported file, skipped: {path}", file=sys.stderr)
        else:
            sources.append(src)
    result = add_sources(bank, index, sources, label=args.source)
    _print_ingest(result.added, result.skipped, result.commit)
    return 0


def cmd_ingest_nyttig(args: argparse.Namespace) -> int:
    bank, index = _bank(args)
    result = nyttig_connector.ingest_nyttig(
        bank, index, _nyttig(args), view=args.view, query=args.query, source=args.source, tag=args.tag,
        since=args.since, assessor=args.assessor, min_score=args.min_score, limit=args.limit,
        include_unarchived=args.include_unarchived,
    )
    return _print_nyttig(result, args.json)


def cmd_ingest_nyttig_digests(args: argparse.Namespace) -> int:
    bank, index = _bank(args)
    result = nyttig_connector.ingest_nyttig_digests(bank, index, _nyttig(args), args.series, limit=args.limit)
    return _print_nyttig(result, args.json)


def _nyttig(args: argparse.Namespace) -> NyttigClient:
    client = NyttigClient.from_settings(Settings.from_env(home=args.home))
    if client is None:
        raise ValueError("TOME_NYTTIG_URL is not set")
    return client


def _print_nyttig(result: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    _print_ingest(result["added"], result["skipped"], result["commit"])
    print(f"known {result['known']}, not archived {result['not_archived_count']}, failed {len(result['failed'])}")
    for item in result["not_archived"]:
        print(f"  not archived ({item['archive_status']}): {item['title']} {item['link']}")
    for item in result["failed"]:
        print(f"  failed: {item.get('title') or item['id']}: {item['error']}")
    return 0


def _print_ingest(added: list[str], skipped: list[dict], commit: str | None) -> None:
    print(f"added {len(added)}, skipped {len(skipped)}, commit {commit or 'none'}")
    for rel in added:
        print(f"  + {rel}")
    for item in skipped:
        print(f"  - {item['reason']}: {item['path'] or item['key']}")


def cmd_lint(args: argparse.Namespace) -> int:
    bank, index = _bank(args)
    report = linting.lint(bank, index, limit=args.limit)
    counts = report["counts"]
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for name, count in counts.items():
            print(f"{name:<18} {count}")
    return 1 if any(counts[name] for name in LINT_FAILS) else 0


def cmd_status(args: argparse.Namespace) -> int:
    bank, index = _bank(args)
    report = status_report(bank, index)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    settings = Settings.from_env(home=args.home, host=args.host, port=args.port)
    serve(settings)
    return 0

