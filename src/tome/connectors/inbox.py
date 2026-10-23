"""Inbox connector: files dropped into inbox/ become raw sources, then go."""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..bank import Bank
from ..index import Index
from ..ingest import Source, add_sources, source_from_file


class InboxConnector:
    name = "inbox"

    def __init__(self, bank: Bank, min_age: float = 10.0):
        self.bank = bank
        # Files changed more recently than this may still be arriving (scp,
        # sync clients); they are picked up on a later run.
        self.min_age = min_age

    def fetch(self, **params) -> Iterable[Source]:
        for _, src in self._scan()[0]:
            yield src

    def _scan(self) -> tuple[list[tuple[Path, Source]], list[Path]]:
        """(file, source) pairs, and files that could not be read as sources."""
        pairs: list[tuple[Path, Source]] = []
        left: list[Path] = []
        for path in self._files():
            try:
                src = source_from_file(path, source=self.name)
            except Exception:
                left.append(path)
                continue
            if src is None:
                left.append(path)
            else:
                pairs.append((path, src))
        return pairs, left

    def _files(self) -> list[Path]:
        root = self.bank.inbox_dir
        if not root.is_dir():
            return []
        out: list[Path] = []
        cutoff = time.time() - self.min_age
        for dirpath, dirnames, filenames in os.walk(root):
            base = Path(dirpath)
            dirnames[:] = sorted(d for d in dirnames if _visible(base / d))
            out += [
                base / f for f in filenames
                if _visible(base / f) and (base / f).is_file() and (base / f).stat().st_mtime <= cutoff
            ]
        return sorted(out)


def ingest_inbox(bank: Bank, index: Index, *, min_age: float = 10.0) -> dict[str, Any]:
    """Capture inbox files. Files that were added or are duplicates of raw
    content are removed; unsupported, unreadable and empty files stay."""
    connector = InboxConnector(bank, min_age=min_age)
    pairs, left = connector._scan()
    result = add_sources(bank, index, [src for _, src in pairs], label="inbox")
    # Every non-empty source ends up either added or skipped as a duplicate.
    for path, src in pairs:
        if src.body.strip():
            path.unlink()
        else:
            left.append(path)
    _prune_empty_dirs(bank.inbox_dir)
    return {
        "added": result.added,
        "skipped": result.skipped,
        "left": sorted(p.relative_to(bank.inbox_dir).as_posix() for p in left),
        "commit": result.commit,
    }


def _visible(path: Path) -> bool:
    return not path.name.startswith(".") and not path.is_symlink()


def _prune_empty_dirs(root: Path) -> None:
    for d in sorted(root.rglob("*"), reverse=True):
        if d.is_dir() and not d.is_symlink() and not any(d.iterdir()):
            d.rmdir()
