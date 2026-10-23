from tome.connectors.inbox import InboxConnector, ingest_inbox

from .test_ingest import HTML

NOTE = "Plain note about routers.\n"


def drop(bank, rel, data: bytes | str):
    path = bank.inbox_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def test_files_are_ingested_and_removed(bank, index):
    drop(bank, "a.md", "---\ntitle: Alpha\ndate: 2026-02-02\n---\n\nAlpha body.\n")
    drop(bank, "notes/b.txt", NOTE)
    drop(bank, "page.html", HTML)
    drop(bank, "nested/deep/c.txt", "Deep note.\n")
    result = ingest_inbox(bank, index, min_age=0)
    assert len(result["added"]) == 4
    assert result["skipped"] == []
    assert all(p.startswith("raw/inbox/") for p in result["added"])
    assert result["commit"]
    for rel in ("a.md", "notes/b.txt", "page.html", "nested/deep/c.txt"):
        assert not (bank.inbox_dir / rel).exists()
    assert not (bank.inbox_dir / "notes").exists()
    assert not (bank.inbox_dir / "nested").exists()
    assert bank.inbox_dir.is_dir()
    assert (bank.root / result["added"][0]).is_file()


def test_duplicates_are_removed_and_reported(bank, index):
    drop(bank, "first.txt", NOTE)
    ingest_inbox(bank, index, min_age=0)
    again = drop(bank, "again/first.txt", NOTE)
    twice = drop(bank, "twice.txt", "Second copy in one batch.\n")
    dup_twice = drop(bank, "twice-copy.txt", "Second copy in one batch.\n")
    result = ingest_inbox(bank, index, min_age=0)
    # again/first.txt duplicates raw; twice.txt duplicates twice-copy.txt in this batch.
    assert [s["reason"] for s in result["skipped"]] == ["duplicate", "duplicate"]
    assert all(s["path"].startswith("raw/inbox/") for s in result["skipped"])
    assert len(result["added"]) == 1
    assert not again.exists() and not twice.exists() and not dup_twice.exists()
    assert not (bank.inbox_dir / "again").exists()
    assert result["left"] == []


def test_unsupported_and_empty_files_are_left(bank, index):
    drop(bank, "x.docx", b"PK\x03\x04")
    drop(bank, "empty.txt", "  \n")
    drop(bank, "ok.txt", NOTE)
    result = ingest_inbox(bank, index, min_age=0)
    assert result["left"] == ["empty.txt", "x.docx"]
    assert (bank.inbox_dir / "x.docx").exists()
    assert (bank.inbox_dir / "empty.txt").exists()
    assert not (bank.inbox_dir / "ok.txt").exists()


def test_hidden_and_symlinked_entries_are_ignored(bank, index, tmp_path):
    drop(bank, ".hidden.txt", NOTE)
    drop(bank, ".dot/inside.txt", NOTE)
    outside = tmp_path / "outside.txt"
    outside.write_text(NOTE)
    (bank.inbox_dir / "link.txt").symlink_to(outside)
    result = ingest_inbox(bank, index, min_age=0)
    assert result["added"] == [] and result["left"] == []
    assert (bank.inbox_dir / ".hidden.txt").exists()
    assert (bank.inbox_dir / ".dot/inside.txt").exists()
    assert outside.exists()


def test_empty_inbox_is_a_noop(bank, index):
    result = ingest_inbox(bank, index, min_age=0)
    assert result == {"added": [], "skipped": [], "left": [], "commit": None}


def test_connector_fetch_yields_sources(bank):
    drop(bank, "one.md", "---\ntitle: One\nkey: inbox:one\n---\n\nText.\n")
    drop(bank, "two.txt", NOTE)
    drop(bank, "skip.bin", b"\x00")
    sources = list(InboxConnector(bank, min_age=0).fetch())
    assert [s.source for s in sources] == ["inbox", "inbox"]
    assert sources[0].key == "inbox:one" and sources[0].title == "One"
    assert sources[1].key.startswith("inbox:sha256:")


def test_recent_files_wait_for_a_later_run(bank, index):
    (bank.inbox_dir / "arriving.md").write_text("# Arriving\n\nstill being copied")
    result = ingest_inbox(bank, index)  # default min_age: too fresh to take
    assert result["added"] == [] and (bank.inbox_dir / "arriving.md").exists()
    assert ingest_inbox(bank, index, min_age=0)["added"]
