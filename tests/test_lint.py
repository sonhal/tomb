import pytest

from tome.lint import lint

from .conftest import write_raw


def put_wiki(bank, rel, body):
    with bank.lock():
        path = bank.write_wiki(rel, f"---\ntitle: {rel}\n---\n\n{body}\n")
        bank.commit(f"wiki: {rel}", [path])


@pytest.fixture
def clean(bank, index):
    """Every wiki page cites and links; only the lonely raw source is uncited."""
    write_raw(bank, "raw/nyttig/2026/10/cisco.md", "title: Cisco\nsource: nyttig\ndate: 2026-10-20",
              "# Cisco\n\nRouters are under attack.\n")
    write_raw(bank, "raw/manual/2024/03/sqlite.md", "title: SQLite\nsource: manual\ndate: 2024-03-01",
              "# FTS5\n\nRanks with BM25.\n")
    write_raw(bank, "raw/manual/2025/01/lonely.md", "title: Lonely\nsource: manual\ndate: 2025-01-15",
              "# Lonely\n\nNobody cited me.\n")
    put_wiki(bank, "security/cisco.md",
             "Under attack [@raw/nyttig/2026/10/cisco.md#c0]. See [[sqlite]].")
    put_wiki(bank, "sqlite.md",
             "Ranks [@raw/manual/2024/03/sqlite.md#c0]. Related [[security/cisco]].")
    return index


def test_clean_bank_reports_only_uncited_sources(bank, clean):
    report = lint(bank, clean)
    assert report["counts"] == {
        "raw_tampering": 0, "broken_links": 0, "broken_citations": 0,
        "orphan_pages": 0, "uncited_pages": 0, "uncited_sources": 1,
    }
    assert [s["path"] for s in report["uncited_sources"]] == ["raw/manual/2025/01/lonely.md"]
    assert report["uncited_sources"][0] == {
        "path": "raw/manual/2025/01/lonely.md", "title": "Lonely", "date": "2025-01-15", "source": "manual",
    }


def test_uncited_sources_newest_first(bank, clean):
    write_raw(bank, "raw/manual/2026/05/newer.md", "title: Newer\nsource: manual\ndate: 2026-05-05", "# N\n\nx\n")
    paths = [s["path"] for s in lint(bank, clean)["uncited_sources"]]
    assert paths == ["raw/manual/2026/05/newer.md", "raw/manual/2025/01/lonely.md"]


def test_broken_links(bank, clean):
    put_wiki(bank, "broken.md", "Points at [[missing-page]] and [[sqlite]].")
    report = lint(bank, clean)
    assert report["broken_links"] == [{"src": "wiki/broken.md", "dst": "wiki/missing-page.md"}]


def test_broken_citations(bank, clean):
    put_wiki(bank, "bad-cites.md",
             "Bad anchor [@raw/manual/2024/03/sqlite.md#c99]. Missing doc [@raw/manual/nope.md]. "
             "Wiki target [@wiki/sqlite.md]. Good [@raw/manual/2024/03/sqlite.md#c0].")
    report = lint(bank, clean)
    assert sorted(report["broken_citations"], key=lambda c: c["dst"]) == [
        {"src": "wiki/bad-cites.md", "dst": "raw/manual/2024/03/sqlite.md", "anchor": "c99"},
        {"src": "wiki/bad-cites.md", "dst": "raw/manual/nope.md", "anchor": ""},
        {"src": "wiki/bad-cites.md", "dst": "wiki/sqlite.md", "anchor": ""},
    ]


def test_orphan_pages(bank, clean):
    put_wiki(bank, "orphan.md", "Nobody links here, but cites [@raw/manual/2024/03/sqlite.md#c0].")
    report = lint(bank, clean)
    assert report["orphan_pages"] == ["wiki/orphan.md"]


def test_self_link_is_not_an_incoming_link(bank, clean):
    put_wiki(bank, "loop.md", "Cites [@raw/manual/2024/03/sqlite.md#c0]. Self [[loop]].")
    assert "wiki/loop.md" in lint(bank, clean)["orphan_pages"]


def test_uncited_pages(bank, clean):
    put_wiki(bank, "opinion.md", "I think [[sqlite]] is great.")
    report = lint(bank, clean)
    assert report["uncited_pages"] == ["wiki/opinion.md"]


def test_raw_tampering(bank, clean):
    raw = bank.root / "raw/nyttig/2026/10/cisco.md"
    raw.chmod(0o644)
    raw.write_text("edited behind our back")
    (bank.raw_dir / "manual" / "stray.md").write_text("untracked")
    report = lint(bank, clean)
    assert len(report["raw_tampering"]) == 2
    assert report["counts"]["raw_tampering"] == 2


def test_limit_truncates_lists_but_not_counts(bank, clean):
    for i in range(3):
        put_wiki(bank, f"broken-{i}.md", f"[[nowhere-{i}]]")
    report = lint(bank, clean, limit=2)
    assert len(report["broken_links"]) == 2
    assert report["counts"]["broken_links"] == 3
