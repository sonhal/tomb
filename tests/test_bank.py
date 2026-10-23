import os
import stat

import pytest

from tome.bank import Bank, PathError

from .conftest import write_raw


def test_init_creates_layout_and_commit(bank):
    for rel in ("TOME.md", "wiki/index.md", "wiki/log.md", "raw/.gitkeep", ".gitignore"):
        assert (bank.root / rel).is_file()
    assert bank.git("log", "--oneline").stdout.count("\n") == 1
    assert bank.is_initialized()


def test_init_is_idempotent(bank):
    (bank.root / "wiki/index.md").write_text("mine")
    Bank.init(bank.root)
    assert (bank.root / "wiki/index.md").read_text() == "mine"


@pytest.mark.parametrize("bad", [
    "", "/etc/passwd.md", "../x.md", "wiki/../raw/x.md", "raw/x.md", "a/./b.md",
    ".hidden.md", "wiki/.git/x.md", "page.txt", "wiki", "a\\b.md", "x\x00.md",
])
def test_wiki_path_rejects(bank, bad):
    with pytest.raises(PathError):
        bank.wiki_path(bad)


def test_wiki_path_accepts_both_spellings(bank):
    assert bank.wiki_path("topics/x.md")[1] == "wiki/topics/x.md"
    assert bank.wiki_path("wiki/topics/x.md")[1] == "wiki/topics/x.md"


def test_wiki_path_rejects_symlink_escape(bank, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, bank.wiki_dir / "link")
    with pytest.raises(PathError):
        bank.wiki_path("link/x.md")


def test_raw_is_create_only_and_read_only(bank):
    path = write_raw(bank, "raw/manual/2026/10/a.md", "title: A", "body")
    mode = (bank.root / path).stat().st_mode
    assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    with bank.lock(), pytest.raises(FileExistsError):
        bank.create_raw(path, "again")
    with pytest.raises(PathError):
        bank.create_raw("wiki/x.md", "nope")
    with pytest.raises(PathError):
        bank.create_raw("raw/../wiki/x.md", "nope")


def test_commit_only_touches_given_paths(bank):
    (bank.root / "wiki/scratch.md").write_text("not mine to commit")
    with bank.lock():
        rel = bank.write_wiki("topic.md", "# Topic\n")
        sha = bank.commit("wiki: add topic", [rel])
    assert sha
    shown = bank.git("show", "--name-only", "--format=%an %s", "HEAD").stdout
    assert "tome wiki: add topic" in shown
    assert "wiki/topic.md" in shown and "scratch" not in shown
    # Nothing changed: no empty commit.
    with bank.lock():
        assert bank.commit("noop", [rel]) is None


def test_delete_wiki_commits_removal(bank):
    with bank.lock():
        rel = bank.write_wiki("gone.md", "x")
        bank.commit("add", [rel])
        bank.delete_wiki("gone.md")
        assert bank.commit("delete", [rel])
    assert not (bank.root / rel).exists()
    with pytest.raises(PathError):
        bank.delete_wiki("index.md")


def test_raw_changes_detects_tampering(bank):
    path = write_raw(bank, "raw/manual/2026/10/a.md", "title: A", "body")
    assert bank.raw_changes() == []
    p = bank.root / path
    p.chmod(0o644)
    p.write_text("edited")
    (bank.root / "raw/stray.md").write_text("added by hand")
    changes = bank.raw_changes()
    assert any("a.md" in c for c in changes) and any("stray.md" in c for c in changes)


def test_append_log(bank):
    bank.append_log("capture(test): 1 source")
    assert "capture(test): 1 source" in (bank.root / "wiki/log.md").read_text().splitlines()[-1]


@pytest.mark.parametrize("bad", [
    "", "/etc/passwd.md", "../x.md", "raw/../wiki/x.md", "wiki", "raw/.hidden.md",
    "raw/x.txt", "other/x.md", "x.md", "raw/x.md/", "a\\b.md",
])
def test_read_path_rejects(bank, bad):
    with pytest.raises(PathError):
        bank.read_path(bad)


def test_read_path_accepts_raw_and_wiki(bank):
    write_raw(bank, "raw/manual/2026/10/a.md", "title: A", "body")
    assert bank.read_path("raw/manual/2026/10/a.md")[1] == "raw/manual/2026/10/a.md"
    assert bank.read_path("wiki/index.md")[1] == "wiki/index.md"


def test_read_path_rejects_symlink_escape(bank, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret")
    os.symlink(outside, bank.raw_dir / "link")
    with pytest.raises(PathError):
        bank.read_path("raw/link/secret.md")


def test_log_page_is_append_only(bank):
    with pytest.raises(PathError):
        bank.write_wiki("log.md", "rewritten history")
    assert bank.write_wiki("index.md", "# Index\n") == "wiki/index.md"
