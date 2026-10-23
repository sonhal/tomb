import pytest

from tome.index import Index, QueryError, parse_when

from .conftest import write_raw


@pytest.fixture
def filled(bank, index):
    write_raw(bank, "raw/nyttig/2026/10/cisco.md",
              "title: Cisco IOS flaw exploited\nsource: nyttig\ndate: 2026-10-20\ntags: [cve, cisco]\nkey: nyttig:item:1",
              "# Cisco IOS flaw exploited\n\nAttackers exploit a remote code execution bug in Cisco IOS routers.\n")
    write_raw(bank, "raw/manual/2024/03/sqlite.md",
              "title: SQLite full-text search\nsource: manual\ndate: 2024-03-01\ntags: [sqlite]",
              "# FTS5\n\nFTS5 ranks results with BM25.\n\n# Tokenizers\n\nThe unicode61 tokenizer folds diacritics: blåbær.\n")
    with bank.lock():
        rel = bank.write_wiki("security/cisco.md",
                              "---\ntitle: Cisco\ntags: [cve]\nupdated: 2026-10-21\n---\n\n"
                              "Routers are under attack [@raw/nyttig/2026/10/cisco.md#c0]. See [[sqlite]].\n")
        bank.commit("wiki: cisco", [rel])
    index.sync(force=True)
    return index


def test_sync_counts_and_is_incremental(filled):
    s = filled.stats()
    assert (s["raw"], s["wiki"]) == (2, 3)  # wiki: index, log, cisco
    stats = filled.sync(force=True)
    assert stats.added == stats.updated == stats.removed == 0


def test_sync_picks_up_edits_and_deletes(bank, filled):
    page = bank.root / "wiki/security/cisco.md"
    page.write_text("# Cisco\n\nNow about switches.\n")
    stats = filled.sync(force=True)
    assert stats.updated == 1
    assert filled.search("switches")[0]["path"] == "wiki/security/cisco.md"
    page.unlink()
    assert filled.sync(force=True).removed == 1
    assert filled.search("switches") == []


def test_sync_throttle(bank, filled):
    filled.min_sync_interval = 3600
    assert filled.sync() is None


def test_rebuild_from_scratch(bank, filled):
    bank.db_path.unlink()
    fresh = Index(bank, min_sync_interval=0)
    fresh.sync(force=True)
    assert fresh.stats() == filled.stats()


def test_search_natural_and_fallback(filled):
    hits = filled.search("how is the Cisco router exploited?")
    assert hits[0]["path"] == "raw/nyttig/2026/10/cisco.md"
    assert hits[0]["chunk"] == "raw/nyttig/2026/10/cisco.md#c0"
    assert "**" in hits[0]["snippet"]
    # No document has both words: the AND pass finds nothing, OR still does.
    paths = {h["path"] for h in filled.search("cisco bm25")}
    assert {"raw/nyttig/2026/10/cisco.md", "raw/manual/2024/03/sqlite.md"} <= paths


def test_search_diacritics_folded(filled):
    assert filled.search("blabær")[0]["heading"] == "Tokenizers"  # å folds to a
    assert filled.search("blåbær")[0]["heading"] == "Tokenizers"


def test_search_filters(filled):
    assert {h["kind"] for h in filled.search("cisco", kind="wiki")} == {"wiki"}
    assert all(h["source"] == "nyttig" for h in filled.search("cisco", source="nyttig"))
    assert filled.search("cisco", tags=["cisco", "cve"])[0]["path"].startswith("raw/")
    assert filled.search("fts5", since="2025-01-01") == []
    assert filled.search("fts5", until="2024-03-01")
    assert filled.search("cisco", prefix="wiki/security/")[0]["path"] == "wiki/security/cisco.md"


def test_search_bad_fts_syntax(filled):
    with pytest.raises(QueryError):
        filled.search('"unbalanced', syntax="fts")
    assert filled.search('"unbalanced quote (and) parens*') == []  # natural mode escapes


def test_list_documents_and_uncited(filled):
    raw = filled.list_documents(kind="raw")
    assert raw["total"] == 2
    assert raw["documents"][0]["path"] == "raw/nyttig/2026/10/cisco.md"  # newest first
    uncited = filled.list_documents(uncited=True)
    assert [d["path"] for d in uncited["documents"]] == ["raw/manual/2024/03/sqlite.md"]


def test_backlinks_and_document(filled):
    links = filled.backlinks("raw/nyttig/2026/10/cisco.md")
    assert links == [{"src": "wiki/security/cisco.md", "kind": "cite", "anchor": "c0", "title": "Cisco"}]
    assert filled.backlinks("wiki/sqlite.md")[0]["kind"] == "link"
    doc = filled.document("raw/nyttig/2026/10/cisco.md")
    assert doc["key"] == "nyttig:item:1" and doc["tags"] == ["cve", "cisco"]
    assert doc["chunks"][0]["id"] == "raw/nyttig/2026/10/cisco.md#c0"
    assert filled.find_key("nyttig:item:1") == "raw/nyttig/2026/10/cisco.md"


def test_parse_when():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    assert parse_when("7d", now).startswith("2026-10-16")
    assert parse_when("2024-01-01") == "2024-01-01"
    assert parse_when(None) is None


def test_search_prefix_fallback(filled):
    # The source only says "Attackers"; it is found through the prefix "attack*".
    hits = filled.search("attack routers", kind="raw")
    assert hits and hits[0]["path"] == "raw/nyttig/2026/10/cisco.md"
