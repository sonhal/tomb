"""SQLite index over the bank: documents, chunks, tags, links and FTS5.

The index is derived data. Files are the source of truth; `sync()` brings the
index in line with them and a schema change simply rebuilds it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import markdown as md
from .bank import RAW, WIKI, Bank

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS documents (
    path    TEXT PRIMARY KEY,
    kind    TEXT NOT NULL,          -- raw | wiki
    title   TEXT NOT NULL,
    date    TEXT,                   -- ISO 8601: published (raw) or updated (wiki)
    source  TEXT,
    url     TEXT,
    author  TEXT,
    key     TEXT,                   -- stable external id, for dedupe on capture
    tags    TEXT NOT NULL DEFAULT '[]',
    meta    TEXT NOT NULL DEFAULT '{}',
    sha     TEXT NOT NULL,
    mtime   REAL NOT NULL,
    size    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS documents_key ON documents(key);
CREATE INDEX IF NOT EXISTS documents_kind_date ON documents(kind, date);
CREATE TABLE IF NOT EXISTS chunks (
    rowid   INTEGER PRIMARY KEY,
    id      TEXT NOT NULL UNIQUE,   -- <path>#c<N>
    path    TEXT NOT NULL,
    ord     INTEGER NOT NULL,
    heading TEXT NOT NULL,
    line    INTEGER NOT NULL,
    text    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
CREATE TABLE IF NOT EXISTS doc_tags (
    path TEXT NOT NULL,
    tag  TEXT NOT NULL,
    PRIMARY KEY (path, tag)
);
CREATE INDEX IF NOT EXISTS doc_tags_tag ON doc_tags(tag);
CREATE TABLE IF NOT EXISTS links (
    src    TEXT NOT NULL,
    dst    TEXT NOT NULL,
    anchor TEXT NOT NULL DEFAULT '',
    kind   TEXT NOT NULL,           -- link ([[wiki]]) | cite ([@raw])
    PRIMARY KEY (src, dst, anchor, kind)
);
CREATE INDEX IF NOT EXISTS links_dst ON links(dst);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    title, heading, text,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

# Natural-language queries drop these when other terms remain. English and
# Norwegian, kept short: BM25's idf already discounts common words.
_STOPWORDS = frozenset("""
a an and are as at be by do does for from how i in is it of on or that the
this to was what when where which who why with
og i på er en et det den de som til av for med har var om hva hvordan
""".split())

_RELATIVE = re.compile(r"^(\d+)\s*([hdwmy])$")


@dataclass
class SyncStats:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


class QueryError(ValueError):
    """A search query or filter SQLite could not use."""


def parse_when(value: str | None, now: datetime | None = None) -> str | None:
    """ISO date/time as-is, or a relative window ("7d", "12h", "2w", "3m",
    "1y") turned into an ISO timestamp that far back from now."""
    if not value:
        return None
    m = _RELATIVE.match(value.strip().lower())
    if not m:
        return value.strip()
    n, unit = int(m.group(1)), m.group(2)
    days = {"h": n / 24, "d": n, "w": 7 * n, "m": 30 * n, "y": 365 * n}[unit]
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=days)).isoformat()


def _natural_terms(query: str) -> list[str]:
    terms = [t.lower() for t in re.findall(r"\w+", query)]
    kept = [t for t in terms if t not in _STOPWORDS]
    return list(dict.fromkeys(kept or terms))


def _quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _prefix(term: str) -> str:
    """Prefix query for terms long enough not to match half the corpus."""
    return _quote(term) + ("*" if len(term) >= 4 else "")


def _like_prefix(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(t).strip() for t in value if str(t).strip()))


class Index:
    def __init__(self, bank: Bank, min_sync_interval: float = 10.0):
        self.bank = bank
        self.min_sync_interval = min_sync_interval
        self._sync_lock = threading.Lock()
        self._last_sync = 0.0
        self._ensure_schema()

    # ── connection and schema ──────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        self.bank.state_dir.mkdir(exist_ok=True)
        conn = sqlite3.connect(self.bank.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def _db(self):
        """A connection that commits on success and is always closed."""
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._db() as conn:
            conn.executescript("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);")
            row = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if row and row["value"] != str(SCHEMA_VERSION):
                for t in ("documents", "chunks", "doc_tags", "links", "chunks_fts"):
                    conn.execute(f"DROP TABLE IF EXISTS {t}")
            conn.executescript(_SCHEMA)
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))

    # ── sync ───────────────────────────────────────────────────

    def _walk(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for top in (self.bank.raw_dir, self.bank.wiki_dir):
            if not top.is_dir():
                continue
            for p in top.rglob("*.md"):
                rel = p.relative_to(self.bank.root)
                if any(part.startswith(".") for part in rel.parts) or p.is_symlink() or not p.is_file():
                    continue
                found[rel.as_posix()] = p
        return found

    def sync(self, force: bool = False) -> SyncStats | None:
        """Re-index changed files and drop deleted ones. Without `force`, does
        nothing if a sync ran within `min_sync_interval` seconds."""
        if not force and time.monotonic() - self._last_sync < self.min_sync_interval:
            return None
        with self._sync_lock:
            stats = SyncStats()
            files = self._walk()
            with self._db() as conn:
                known = {r["path"]: r for r in conn.execute("SELECT path, sha, mtime, size FROM documents")}
                for rel in known.keys() - files.keys():
                    self._remove(conn, rel)
                    stats.removed += 1
                for rel, path in files.items():
                    st = path.stat()
                    row = known.get(rel)
                    if row and row["mtime"] == st.st_mtime and row["size"] == st.st_size:
                        stats.unchanged += 1
                        continue
                    result = self._index_file(conn, rel, path, row)
                    setattr(stats, result, getattr(stats, result) + 1)
            self._last_sync = time.monotonic()
            return stats

    def update_paths(self, paths: list[str]) -> None:
        """Re-index (or drop) specific bank paths right after a write."""
        with self._sync_lock, self._db() as conn:
            for rel in paths:
                if not rel.endswith(".md") or rel.split("/", 1)[0] not in (RAW, WIKI):
                    continue
                path = self.bank.root / rel
                row = conn.execute("SELECT path, sha, mtime, size FROM documents WHERE path=?", (rel,)).fetchone()
                if path.is_file():
                    self._index_file(conn, rel, path, row)
                elif row:
                    self._remove(conn, rel)

    def _remove(self, conn: sqlite3.Connection, rel: str) -> None:
        conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT rowid FROM chunks WHERE path=?)", (rel,))
        conn.execute("DELETE FROM chunks WHERE path=?", (rel,))
        conn.execute("DELETE FROM doc_tags WHERE path=?", (rel,))
        conn.execute("DELETE FROM links WHERE src=?", (rel,))
        conn.execute("DELETE FROM documents WHERE path=?", (rel,))

    def _index_file(self, conn: sqlite3.Connection, rel: str, path: Path, row) -> str:
        data = path.read_bytes()
        st = path.stat()
        sha = hashlib.sha256(data).hexdigest()
        if row and row["sha"] == sha:
            conn.execute("UPDATE documents SET mtime=?, size=? WHERE path=?", (st.st_mtime, st.st_size, rel))
            return "unchanged"
        self._remove(conn, rel)
        text = data.decode("utf-8", errors="replace")
        meta, body = md.split_frontmatter(text)
        kind = rel.split("/", 1)[0]
        title = str(meta.get("title") or md.first_heading(body) or Path(rel).stem)
        date = md.to_iso(meta.get("date") or meta.get("updated"))
        tags = _tags(meta.get("tags"))
        conn.execute(
            "INSERT INTO documents (path, kind, title, date, source, url, author, key, tags, meta, sha, mtime, size)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rel, kind, title, date,
                _str(meta.get("source")), _str(meta.get("url")), _str(meta.get("author")), _str(meta.get("key")),
                json.dumps(tags), json.dumps(meta, default=str, ensure_ascii=False),
                sha, st.st_mtime, st.st_size,
            ),
        )
        conn.executemany("INSERT OR IGNORE INTO doc_tags VALUES (?, ?)", [(rel, t) for t in tags])
        for c in md.chunk(body):
            cur = conn.execute(
                "INSERT INTO chunks (id, path, ord, heading, line, text) VALUES (?,?,?,?,?,?)",
                (f"{rel}#{c.anchor}", rel, c.ord, c.heading, c.line, c.text),
            )
            conn.execute(
                "INSERT INTO chunks_fts (rowid, title, heading, text) VALUES (?,?,?,?)",
                (cur.lastrowid, title, c.heading, c.text),
            )
        links = [(rel, dst, "", "link") for dst in md.wikilinks(body)]
        links += [(rel, dst, anchor or "", "cite") for dst, anchor in md.citations(body)]
        conn.executemany("INSERT OR IGNORE INTO links VALUES (?,?,?,?)", links)
        return "updated" if row else "added"

    # ── queries ────────────────────────────────────────────────

    def _filters(
        self, *, kind=None, tags=None, source=None, since=None, until=None, prefix=None,
    ) -> tuple[list[str], list[Any]]:
        where, args = [], []
        if kind:
            where.append("d.kind = ?")
            args.append(kind)
        if source:
            where.append("d.source = ?")
            args.append(source)
        for tag in _tags(tags):
            where.append("EXISTS (SELECT 1 FROM doc_tags t WHERE t.path = d.path AND t.tag = ?)")
            args.append(tag)
        if since := parse_when(since):
            where.append("d.date >= ?")
            args.append(since)
        if until := parse_when(until):
            # Compare on the given precision so until=2024-12-31 includes that day.
            where.append("substr(d.date, 1, ?) <= ?")
            args += [len(until), until]
        if prefix:
            where.append("d.path LIKE ? ESCAPE '\\'")
            args.append(_like_prefix(prefix))
        return where, args

    def search(
        self,
        query: str,
        *,
        kind: str | None = None,
        tags: list[str] | str | None = None,
        source: str | None = None,
        since: str | None = None,
        until: str | None = None,
        prefix: str | None = None,
        limit: int = 10,
        per_doc: int = 2,
        syntax: str = "natural",
    ) -> list[dict[str, Any]]:
        """BM25 search over chunks. "natural" requires all terms first and
        widens to any term if that finds too little; "fts" passes FTS5 syntax."""
        limit = max(1, min(limit, 100))
        if syntax == "fts":
            attempts = [query]
        elif syntax == "natural":
            terms = _natural_terms(query)
            if not terms:
                return []
            # No stemmer (it would be English-only): widen step by step from
            # all exact terms, to all terms as prefixes, to any term as a prefix.
            prefixed = [_prefix(t) for t in terms]
            attempts = [" ".join(map(_quote, terms)), " ".join(prefixed)]
            if len(terms) > 1:
                attempts.append(" OR ".join(prefixed))
        else:
            raise QueryError(f"unknown syntax {syntax!r}; use 'natural' or 'fts'")

        where, args = self._filters(kind=kind, tags=tags, source=source, since=since, until=until, prefix=prefix)
        sql = (
            "SELECT c.id, c.path, c.ord, c.heading, c.line, d.kind, d.title, d.date, d.source, d.url, d.tags,"
            " snippet(chunks_fts, 2, '**', '**', ' … ', 32) AS snippet,"
            " bm25(chunks_fts, 5.0, 2.0, 1.0) AS rank"
            " FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid JOIN documents d ON d.path = c.path"
            " WHERE chunks_fts MATCH ?" + "".join(f" AND {w}" for w in where) +
            " ORDER BY rank LIMIT ?"
        )
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        per_path: dict[str, int] = {}
        with self._db() as conn:
            for fts_query in attempts:
                try:
                    rows = conn.execute(sql, [fts_query, *args, limit * 5]).fetchall()
                except sqlite3.OperationalError as e:
                    raise QueryError(f"bad query: {e}") from e
                for r in rows:
                    if r["id"] in seen or per_path.get(r["path"], 0) >= per_doc:
                        continue
                    seen.add(r["id"])
                    per_path[r["path"]] = per_path.get(r["path"], 0) + 1
                    results.append({
                        "chunk": r["id"], "path": r["path"], "kind": r["kind"], "title": r["title"],
                        "heading": r["heading"], "date": r["date"], "source": r["source"], "url": r["url"],
                        "tags": json.loads(r["tags"]), "snippet": r["snippet"], "score": round(-r["rank"], 4),
                    })
                    if len(results) >= limit:
                        return results
        return results

    def list_documents(
        self,
        *,
        kind: str | None = None,
        tags: list[str] | str | None = None,
        source: str | None = None,
        since: str | None = None,
        until: str | None = None,
        prefix: str | None = None,
        uncited: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Documents newest first. `uncited` keeps raw sources no wiki page cites."""
        where, args = self._filters(kind=kind, tags=tags, source=source, since=since, until=until, prefix=prefix)
        if uncited:
            where.append("d.kind = 'raw' AND NOT EXISTS (SELECT 1 FROM links l WHERE l.kind='cite' AND l.dst=d.path)")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        limit = max(1, min(limit, 500))
        with self._db() as conn:
            total = conn.execute(f"SELECT count(*) FROM documents d{clause}", args).fetchone()[0]
            rows = conn.execute(
                "SELECT d.path, d.kind, d.title, d.date, d.source, d.url, d.tags,"
                " (SELECT count(*) FROM chunks c WHERE c.path = d.path) AS chunks"
                f" FROM documents d{clause} ORDER BY coalesce(d.date, '') DESC, d.path LIMIT ? OFFSET ?",
                [*args, limit, max(0, offset)],
            ).fetchall()
        docs = [{**dict(r), "tags": json.loads(r["tags"])} for r in rows]
        return {"total": total, "documents": docs}

    def document(self, path: str) -> dict[str, Any] | None:
        """A document's index entry with its chunk outline."""
        with self._db() as conn:
            row = conn.execute("SELECT * FROM documents WHERE path=?", (path,)).fetchone()
            if not row:
                return None
            chunks = conn.execute(
                "SELECT id, ord, heading, line FROM chunks WHERE path=? ORDER BY ord", (path,)
            ).fetchall()
        doc = {k: row[k] for k in ("path", "kind", "title", "date", "source", "url", "author", "key")}
        doc["tags"] = json.loads(row["tags"])
        doc["meta"] = json.loads(row["meta"])
        doc["chunks"] = [dict(c) for c in chunks]
        return doc

    def chunk(self, chunk_id: str) -> dict[str, Any] | None:
        with self._db() as conn:
            row = conn.execute("SELECT id, path, ord, heading, line, text FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        return dict(row) if row else None

    def backlinks(self, path: str) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT l.src, l.kind, l.anchor, d.title FROM links l LEFT JOIN documents d ON d.path = l.src"
                " WHERE l.dst = ? ORDER BY l.src, l.anchor",
                (path,),
            ).fetchall()
        return [dict(r) for r in rows]

    def find_key(self, key: str) -> str | None:
        with self._db() as conn:
            row = conn.execute("SELECT path FROM documents WHERE key=?", (key,)).fetchone()
        return row["path"] if row else None

    def stats(self) -> dict[str, Any]:
        with self._db() as conn:
            kinds = dict(conn.execute("SELECT kind, count(*) FROM documents GROUP BY kind").fetchall())
            chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        return {"raw": kinds.get("raw", 0), "wiki": kinds.get("wiki", 0), "chunks": chunks}


def _str(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)
