"""Capture: turn sources into raw/ documents, one commit per batch.

Connectors yield `Source`s; `add_sources()` is the only place raw files are
written. Converters (HTML via trafilatura, PDF via pypdf) live here too.
"""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import trafilatura
from pypdf import PdfReader

from . import markdown as md
from .bank import LOG_PAGE, RAW, Bank
from .index import Index

_SOURCE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_CORE_KEYS = ("title", "source", "url", "author", "date", "tags", "key", "ingested", "content_sha256")
_LOG_PATHS = 5
MAX_FILE_BYTES = 50 * 1024 * 1024  # nyttig's archive_max_bytes default
_SUFFIXES = (".md", ".markdown", ".txt", ".html", ".htm", ".pdf")


@dataclass
class Source:
    title: str
    body: str  # Markdown
    source: str  # connector name: nyttig, inbox, manual, ...
    key: str | None = None  # stable external id for dedupe
    url: str | None = None
    author: str | None = None
    date: datetime | date | str | None = None  # published
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # connector-specific frontmatter


@dataclass
class IngestResult:
    added: list[str]
    skipped: list[dict]  # {"key", "path", "reason"}
    commit: str | None


def add_sources(bank: Bank, index: Index, sources: Iterable[Source], *, label: str) -> IngestResult:
    """Write new sources to raw/ under one lock and one commit."""
    items = list(sources)
    for s in items:
        if not _SOURCE.match(s.source):
            raise ValueError(f"bad source name {s.source!r}")
    index.sync(force=True)
    now = datetime.now(timezone.utc)
    added: list[str] = []
    skipped: list[dict] = []
    with bank.lock():
        try:
            _write_all(bank, index, items, now, added, skipped)
        except BaseException:
            # Leave raw/ as it was: an uncommitted raw file would read as tampering.
            for rel in added:
                (bank.root / rel).unlink(missing_ok=True)
            raise
        commit = None
        if added:
            shown = ", ".join(added[:_LOG_PATHS])
            more = f", …and {len(added) - _LOG_PATHS} more" if len(added) > _LOG_PATHS else ""
            bank.append_log(f"capture({label}): {len(added)} source(s): {shown}{more}")
            message = f"ingest({label}): {len(added)} source(s)\n\n" + "\n".join(added)
            commit = bank.commit(message, [*added, LOG_PAGE])
    if added:
        index.update_paths([*added, LOG_PAGE])
    return IngestResult(added=added, skipped=skipped, commit=commit)


def _write_all(
    bank: Bank, index: Index, items: list[Source], now: datetime, added: list[str], skipped: list[dict],
) -> None:
    seen: dict[str, str] = {}  # key -> path, for dedupe within this batch
    for s in items:
        if not s.body.strip():
            skipped.append({"key": s.key, "path": None, "reason": "empty"})
            continue
        if s.key:
            existing = seen.get(s.key) or index.find_key(s.key)
            if existing:
                skipped.append({"key": s.key, "path": existing, "reason": "duplicate"})
                continue
        rel = _raw_path(bank, s, now, added)
        bank.create_raw(rel, _document(s, now))
        added.append(rel)
        if s.key:
            seen[s.key] = rel


def _raw_path(bank: Bank, s: Source, now: datetime, taken: list[str]) -> str:
    when = _published(s.date) or now
    base = f"{RAW}/{s.source}/{when.year:04d}/{when.month:02d}/{md.slugify(s.title)}"
    rel, n = f"{base}.md", 2
    while rel in taken or (bank.root / rel).exists():
        rel = f"{base}-{n}.md"
        n += 1
    return rel


def _published(value: datetime | date | str | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _document(s: Source, now: datetime) -> str:
    body = s.body.strip()
    if not body.startswith("# "):
        body = f"# {s.title}\n\n{body}"
    core: dict[str, Any] = {
        "title": s.title,
        "source": s.source,
        "url": s.url,
        "author": s.author,
        "date": md.to_iso(s.date),
        "tags": s.tags or None,
        "key": s.key,
        "ingested": now.replace(microsecond=0).isoformat(),
        "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }
    meta = {k: core[k] for k in _CORE_KEYS if core[k] is not None}
    meta.update({k: v for k, v in s.extra.items() if k not in _CORE_KEYS and v is not None})
    return md.render(meta, body)


def html_to_markdown(html: str | bytes, url: str | None = None) -> tuple[str, dict]:
    """Main article text as Markdown, plus title/author/date/url when found."""
    try:
        text = trafilatura.extract(
            html, output_format="markdown", include_links=True, include_tables=True, url=url,
        ) or ""
        if not text.strip():
            return "", {}
        doc = trafilatura.extract_metadata(html, default_url=url)
    except Exception:
        return "", {}
    if doc is None:
        return text, {"title": None, "author": None, "date": None, "url": url}
    return text, {"title": doc.title, "author": doc.author, "date": doc.date, "url": doc.url or url}


def pdf_to_text(data: bytes) -> tuple[str, dict]:
    """Page texts joined with blank lines, plus the PDF's title/author."""
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
        info = reader.metadata
        meta = {"title": info.title if info else None, "author": info.author if info else None}
    except Exception:
        return "", {}
    return "\n\n".join(p for p in pages if p), meta


def source_from_file(path: Path, *, source: str, tags: list[str] | None = None) -> Source | None:
    """A Source for a supported file (.md, .txt, .html, .pdf), else None."""
    suffix = path.suffix.lower()
    if suffix not in _SUFFIXES:
        return None
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{path.name} is larger than {MAX_FILE_BYTES} bytes")
    data = path.read_bytes()
    fm: dict[str, Any] = {}
    extracted: dict[str, Any] = {}
    if suffix in (".md", ".markdown"):
        fm, body = md.split_frontmatter(data.decode("utf-8", errors="replace"))
        body = body.strip()
    elif suffix == ".txt":
        body = data.decode("utf-8", errors="replace")
    elif suffix in (".html", ".htm"):
        body, extracted = html_to_markdown(data)
    elif suffix == ".pdf":
        body, extracted = pdf_to_text(data)
    else:
        return None

    def pick(k: str) -> Any:
        return fm.get(k) if fm.get(k) is not None else extracted.get(k)

    title = (
        pick("title") or md.first_heading(body) or path.stem.replace("-", " ").replace("_", " ").strip()
    )
    fm_tags = fm.get("tags") or []
    if isinstance(fm_tags, str):
        fm_tags = fm_tags.split(",")
    elif not isinstance(fm_tags, list):
        fm_tags = []
    all_tags = list(dict.fromkeys([*(str(t).strip() for t in fm_tags), *(tags or [])]))
    key = fm.get("key") or f"{source}:sha256:{hashlib.sha256(data).hexdigest()}"
    extra = {k: v for k, v in fm.items() if k not in ("title", "source", "url", "author", "date", "tags", "key")}
    return Source(
        title=str(title),
        body=body,
        source=source,
        key=str(key),
        url=_text(pick("url")),
        author=_text(pick("author")),
        date=pick("date"),
        tags=[t for t in all_tags if t],
        extra=extra,
    )


def _text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)
