"""Lint: checks on the bank's links, citations and integration state."""

from __future__ import annotations

from typing import Any

from .bank import INDEX_PAGE, LOG_PAGE, Bank
from .index import Index

_HOUSEKEEPING = (INDEX_PAGE, LOG_PAGE)

_QUERIES = {
    "broken_links": (
        "SELECT src, dst FROM links WHERE kind='link' AND dst NOT IN (SELECT path FROM documents)"
        " ORDER BY src, dst",
        lambda r: {"src": r["src"], "dst": r["dst"]},
    ),
    "broken_citations": (
        "SELECT l.src, l.dst, l.anchor FROM links l WHERE l.kind='cite' AND ("
        " NOT EXISTS (SELECT 1 FROM documents d WHERE d.path = l.dst AND d.kind='raw')"
        " OR (l.anchor <> '' AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.id = l.dst || '#' || l.anchor)))"
        " ORDER BY l.src, l.dst, l.anchor",
        lambda r: {"src": r["src"], "dst": r["dst"], "anchor": r["anchor"]},
    ),
    "orphan_pages": (
        "SELECT d.path FROM documents d WHERE d.kind='wiki' AND d.path NOT IN (?, ?)"
        " AND NOT EXISTS (SELECT 1 FROM links l JOIN documents s ON s.path = l.src"
        "  WHERE l.kind='link' AND l.dst = d.path AND s.kind='wiki' AND l.src <> d.path)"
        " ORDER BY d.path",
        lambda r: r["path"],
    ),
    "uncited_pages": (
        "SELECT d.path FROM documents d WHERE d.kind='wiki' AND d.path NOT IN (?, ?)"
        " AND NOT EXISTS (SELECT 1 FROM links l WHERE l.src = d.path AND l.kind='cite')"
        " ORDER BY d.path",
        lambda r: r["path"],
    ),
    "uncited_sources": (
        "SELECT d.path, d.title, d.date, d.source FROM documents d WHERE d.kind='raw'"
        " AND NOT EXISTS (SELECT 1 FROM links l WHERE l.kind='cite' AND l.dst = d.path)"
        " ORDER BY coalesce(d.date, '') DESC, d.path",
        lambda r: {"path": r["path"], "title": r["title"], "date": r["date"], "source": r["source"]},
    ),
}


def lint(bank: Bank, index: Index, *, limit: int = 50) -> dict[str, Any]:
    """Every problem category, each list truncated to `limit`, plus full counts."""
    index.sync(force=True)
    results: dict[str, list] = {"raw_tampering": bank.raw_changes()}
    with index._db() as conn:
        for name, (sql, row) in _QUERIES.items():
            args = _HOUSEKEEPING if "?" in sql else ()
            results[name] = [row(r) for r in conn.execute(sql, args).fetchall()]
    counts = {k: len(v) for k, v in results.items()}
    return {**{k: v[:limit] for k, v in results.items()}, "counts": counts}
