"""Markdown helpers: frontmatter, slugs, links, citations and chunking."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import yaml

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
# [[target]] or [[target|label]] or [[target#heading]]
_WIKILINK = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")
# [@raw/x/y.md#c3] — a citation of a raw chunk (or a whole raw document)
_CITATION = re.compile(r"\[@([^\]\s#]+?)(?:#(c\d+))?\]")
_WORD = re.compile(r"\S+")


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (metadata, body). Invalid or non-mapping YAML yields {}."""
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, text[m.end():]


def render(meta: dict[str, Any], body: str) -> str:
    """Serialize metadata and body back into a Markdown document."""
    if not meta:
        return body
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{head}---\n\n{body.lstrip()}"


def to_iso(value: Any) -> str | None:
    """Normalize a frontmatter date (YAML may already have parsed it) to ISO 8601."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return s


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return text[:max_len].rstrip("-") or "untitled"


def first_heading(body: str) -> str | None:
    for line in _lines_outside_fences(body):
        m = _HEADING.match(line)
        if m:
            return m.group(2)
    return None


def wikilinks(body: str) -> list[str]:
    """Targets of [[wikilinks]], normalized to bank paths (wiki/<target>.md)."""
    out = []
    for m in _WIKILINK.finditer(body):
        out.append(wiki_target(m.group(1)))
    return list(dict.fromkeys(out))


def wiki_target(target: str) -> str:
    t = target.strip().strip("/")
    if t.startswith("wiki/"):
        t = t[len("wiki/"):]
    if not t.endswith(".md"):
        t += ".md"
    return "wiki/" + t


def citations(body: str) -> list[tuple[str, str | None]]:
    """(document path, chunk anchor or None) for every [@...] citation."""
    out = []
    for m in _CITATION.finditer(body):
        path = m.group(1)
        if not path.endswith(".md"):
            path += ".md"
        out.append((path, m.group(2)))
    return list(dict.fromkeys(out))


def _lines_outside_fences(body: str):
    fenced = False
    for line in body.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield line


@dataclass(frozen=True)
class Chunk:
    ord: int
    heading: str  # "H1 > H2" path of the section the chunk is in
    text: str
    line: int  # 1-based line in the body where the chunk starts

    @property
    def anchor(self) -> str:
        return f"c{self.ord}"


@dataclass
class _Block:
    text: str
    line: int


def _sections(body: str) -> list[tuple[str, list[_Block]]]:
    """Split into (heading path, paragraphs). Fenced code stays one block."""
    sections: list[tuple[str, list[_Block]]] = [("", [])]
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    buf_line = 0
    fenced = False

    def flush():
        nonlocal buf
        text = "\n".join(buf).strip()
        if text:
            sections[-1][1].append(_Block(text, buf_line))
        buf = []

    for i, line in enumerate(body.splitlines(), start=1):
        if _FENCE.match(line):
            if not fenced:
                flush()
            fenced = not fenced
            if not buf:
                buf_line = i
            buf.append(line)
            if not fenced:
                flush()
            continue
        if fenced:
            buf.append(line)
            continue
        m = _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2)))
            sections.append((" > ".join(h for _, h in stack), []))
            continue
        if not line.strip():
            flush()
            continue
        if not buf:
            buf_line = i
        buf.append(line)
    flush()
    return [(h, blocks) for h, blocks in sections if blocks]


def _split_long(block: _Block, max_words: int) -> list[_Block]:
    words = block.text.split()
    if len(words) <= max_words:
        return [block]
    # Prefer sentence boundaries; fall back to hard word cuts.
    sentences = re.split(r"(?<=[.!?])\s+", block.text)
    out: list[_Block] = []
    cur: list[str] = []
    for s in sentences:
        sw = s.split()
        if len(sw) > max_words:
            if cur:
                out.append(_Block(" ".join(cur), block.line))
                cur = []
            for i in range(0, len(sw), max_words):
                out.append(_Block(" ".join(sw[i:i + max_words]), block.line))
            continue
        if len(cur) + len(sw) > max_words and cur:
            out.append(_Block(" ".join(cur), block.line))
            cur = []
        cur.extend(sw)
    if cur:
        out.append(_Block(" ".join(cur), block.line))
    return out


def chunk(body: str, target_words: int = 350, max_words: int = 500, overlap_words: int = 50) -> list[Chunk]:
    """Heading-aware chunks. Chunks never cross sections; within a section
    consecutive chunks share ~overlap_words of trailing context."""
    chunks: list[Chunk] = []
    for heading, blocks in _sections(body):
        pieces: list[_Block] = []
        for b in blocks:
            pieces.extend(_split_long(b, max_words))
        cur: list[_Block] = []
        cur_words = 0
        prev_tail = ""

        def emit():
            nonlocal cur, cur_words, prev_tail
            text = "\n\n".join(b.text for b in cur)
            full = f"{prev_tail}\n\n{text}".strip() if prev_tail else text
            chunks.append(Chunk(len(chunks), heading, full, cur[0].line))
            words = _WORD.findall(text)
            prev_tail = " ".join(words[-overlap_words:]) if overlap_words and len(words) > overlap_words else ""
            cur, cur_words = [], 0

        for p in pieces:
            n = len(p.text.split())
            if cur and cur_words + n > target_words:
                emit()
            cur.append(p)
            cur_words += n
        if cur:
            emit()
    return chunks
