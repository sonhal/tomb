"""The bank: a git repo of Markdown with an immutable raw/ and an agent-owned wiki/.

All writes go through this module. It decides what may be written (wiki/
pages, new raw files only), serializes writers, and commits exactly the paths
it touched so unrelated working-tree changes are never swept into a commit.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path, PurePosixPath

RAW = "raw"
WIKI = "wiki"
INBOX = "inbox"
STATE = ".tome"

INDEX_PAGE = "wiki/index.md"
LOG_PAGE = "wiki/log.md"

_MAX_PATH = 255


class PathError(ValueError):
    """A path the caller may not use."""


class Bank:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).expanduser().resolve()
        self.raw_dir = self.root / RAW
        self.wiki_dir = self.root / WIKI
        self.inbox_dir = self.root / INBOX
        self.state_dir = self.root / STATE
        self.db_path = self.state_dir / "index.db"
        self._thread_lock = threading.RLock()

    # ── setup ──────────────────────────────────────────────────

    def is_initialized(self) -> bool:
        return (self.root / "TOME.md").is_file() and self.raw_dir.is_dir() and self.wiki_dir.is_dir()

    @classmethod
    def init(cls, root: str | os.PathLike) -> "Bank":
        """Create a bank (idempotent: existing files are left alone)."""
        bank = cls(root)
        bank.root.mkdir(parents=True, exist_ok=True)
        for d in (bank.raw_dir, bank.wiki_dir, bank.inbox_dir, bank.state_dir):
            d.mkdir(exist_ok=True)
        template = resources.files("tome").joinpath("templates/TOME.md").read_text()
        files = {
            "TOME.md": template,
            ".gitignore": f"/{STATE}/\n/{INBOX}/\n",
            f"{RAW}/.gitkeep": "",
            INDEX_PAGE: "---\ntitle: Index\n---\n\n# Index\n\nCatalogue of wiki pages, maintained by the agent.\n",
            LOG_PAGE: "---\ntitle: Log\n---\n\n# Log\n\nOne line per capture or integration, newest last.\n\n",
        }
        created = []
        for rel, content in files.items():
            p = bank.root / rel
            if not p.exists():
                p.write_text(content)
                created.append(rel)
        if not (bank.root / ".git").exists():
            bank.git("init", "-q", "-b", "main")
        if created:
            bank.commit("init: create bank", created)
        return bank

    # ── paths ──────────────────────────────────────────────────

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def wiki_path(self, rel: str) -> tuple[Path, str]:
        """Validate a wiki page path ("topics/x.md" or "wiki/topics/x.md").

        Returns (absolute path, bank-relative path). Rejects anything outside
        wiki/, hidden segments, non-.md files and symlinked components.
        """
        if not isinstance(rel, str) or not rel.strip():
            raise PathError("path is empty")
        if "\\" in rel or "\x00" in rel:
            raise PathError("path contains a backslash or NUL")
        if rel.strip().startswith("/"):
            raise PathError("path must be relative to the bank")
        parts = rel.strip().split("/")
        if parts[0] == WIKI:
            parts = parts[1:]
        if not parts:
            raise PathError("path names no page")
        if parts[0] in (RAW, INBOX):
            raise PathError(f"{parts[0]}/ is not writable; wiki pages live under wiki/")
        for part in parts:
            if part in ("", ".", "..") or part.startswith("."):
                raise PathError(f"path segment {part!r} is not allowed")
        if not parts[-1].endswith(".md") or parts[-1] == ".md":
            raise PathError("wiki pages must be .md files")
        bank_rel = "/".join([WIKI, *parts])
        if len(bank_rel) > _MAX_PATH:
            raise PathError("path is too long")
        target = self.root.joinpath(*bank_rel.split("/"))
        self._reject_symlinks(self.wiki_dir, target)
        return target, bank_rel

    def read_path(self, rel: str) -> tuple[Path, str]:
        """Validate a readable document path ("raw/…md" or "wiki/…md").

        Returns (absolute path, bank-relative path). Same rules as wiki_path,
        for both top-level folders.
        """
        if not isinstance(rel, str) or not rel.strip():
            raise PathError("path is empty")
        if "\\" in rel or "\x00" in rel or rel.strip().startswith("/"):
            raise PathError("path must be relative to the bank, with forward slashes")
        parts = rel.strip().split("/")
        if len(parts) < 2 or parts[0] not in (RAW, WIKI):
            raise PathError("path must start with raw/ or wiki/")
        for part in parts:
            if part in ("", ".", "..") or part.startswith("."):
                raise PathError(f"path segment {part!r} is not allowed")
        if not parts[-1].endswith(".md") or parts[-1] == ".md":
            raise PathError("documents are .md files")
        target = self.root.joinpath(*parts)
        self._reject_symlinks(self.root / parts[0], target)
        return target, "/".join(parts)

    def _reject_symlinks(self, base: Path, target: Path) -> None:
        if base.is_symlink():
            raise PathError(f"{base.name}/ is a symlink")
        cur = base
        for part in target.relative_to(base).parts:
            cur = cur / part
            if cur.is_symlink():
                raise PathError(f"{self.rel(cur.parent)}/{part} is a symlink")
        if not target.resolve().is_relative_to(base.resolve()):
            raise PathError("path escapes its directory")

    # ── writes (call inside `with bank.lock()`) ───────────────

    def write_wiki(self, rel: str, content: str) -> str:
        target, bank_rel = self.wiki_path(rel)
        if bank_rel == LOG_PAGE:
            raise PathError(f"{LOG_PAGE} is append-only; add entries with the log tool")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_text(content)
        os.replace(tmp, target)
        return bank_rel

    def delete_wiki(self, rel: str) -> str:
        target, bank_rel = self.wiki_path(rel)
        if bank_rel in (INDEX_PAGE, LOG_PAGE):
            raise PathError(f"{bank_rel} cannot be deleted")
        if not target.is_file():
            raise FileNotFoundError(bank_rel)
        target.unlink()
        return bank_rel

    def create_raw(self, rel: str, content: str) -> str:
        """Create a new raw file. Never overwrites; the file is made read-only."""
        p = PurePosixPath(rel)
        if p.is_absolute() or not p.parts or p.parts[0] != RAW or any(
            s in ("", ".", "..") or s.startswith(".") for s in p.parts
        ) or p.suffix != ".md":
            raise PathError(f"not a raw document path: {rel}")
        target = self.root.joinpath(*p.parts)
        self._reject_symlinks(self.raw_dir, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "x", encoding="utf-8") as f:
            f.write(content)
        target.chmod(0o444)
        return p.as_posix()

    def append_log(self, line: str) -> str:
        """Append one entry to wiki/log.md."""
        target = self.root / LOG_PAGE
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry = f"- {stamp} {line.strip()}\n"
        with open(target, "a", encoding="utf-8") as f:
            f.write(entry)
        return LOG_PAGE

    # ── locking and git ────────────────────────────────────────

    @contextmanager
    def lock(self):
        """Exclusive writer lock, across threads and processes (CLI + server)."""
        self.state_dir.mkdir(exist_ok=True)
        with self._thread_lock:
            with open(self.state_dir / "write.lock", "w") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    yield self
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": os.environ.get("TOME_GIT_NAME", "tome"),
            "GIT_AUTHOR_EMAIL": os.environ.get("TOME_GIT_EMAIL", "tome@localhost"),
        }
        env["GIT_COMMITTER_NAME"] = env["GIT_AUTHOR_NAME"]
        env["GIT_COMMITTER_EMAIL"] = env["GIT_AUTHOR_EMAIL"]
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=check, capture_output=True, text=True, env=env,
        )

    def commit(self, message: str, paths: list[str]) -> str | None:
        """Commit exactly `paths` (added, changed or deleted). Returns the new
        commit sha, or None if those paths had no changes."""
        if not paths:
            return None
        paths = list(dict.fromkeys(paths))
        self.git("add", "-A", "--", *paths)
        staged = self.git("diff", "--cached", "--quiet", "--", *paths, check=False)
        if staged.returncode == 0:
            return None
        self.git("commit", "-q", "-m", message, "--", *paths)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def recent_commits(self, limit: int = 10) -> list[dict[str, str]]:
        """The newest commits, as {sha, date (ISO), subject}. Empty before the first commit."""
        out = self.git("log", f"-{limit}", "--pretty=format:%H%x1f%cI%x1f%s", check=False)
        if out.returncode != 0:
            return []
        keys = ("sha", "date", "subject")
        return [dict(zip(keys, line.split("\x1f"))) for line in out.stdout.splitlines() if line]

    def raw_changes(self) -> list[str]:
        """Uncommitted changes under raw/: anything here means raw was touched
        outside tome (a new raw file is committed in the same step it is made)."""
        out = self.git("status", "--porcelain", "--untracked-files=all", "--", RAW).stdout
        return [line for line in out.splitlines() if line.strip()]
