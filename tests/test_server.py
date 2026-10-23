import pytest
from mcp import Client

from tome.server import create_server

from .conftest import write_raw

RAW = "raw/manual/2026/10/fox.md"
LONG = "# Foxes\n\n" + " ".join(f"Word{i} about foxes and dens." for i in range(400)) + "\n"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def server(bank, index):
    return create_server(bank, index)


def payload(result):
    assert not result.is_error, result.content[0].text
    sc = result.structured_content
    return sc["result"] if set(sc) == {"result"} else sc  # the SDK wraps list results


def error_text(result) -> str:
    assert result.is_error
    return result.content[0].text


@pytest.fixture
def raw_doc(bank):
    write_raw(bank, RAW, "title: Foxes\nsource: manual\ndate: 2026-10-01\ntags: [animals]", "# Heading\n\nSome text about foxes.\n")
    return RAW


@pytest.mark.anyio
async def test_lists_tools_and_instructions(server, bank):
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert client.instructions == (bank.root / "TOME.md").read_text()
    assert names == {
        "search", "read", "list_documents", "backlinks", "write_wiki_page", "delete_wiki_page",
        "log", "ingest_text", "ingest_inbox", "lint", "status",
    }


@pytest.mark.anyio
async def test_write_wiki_page_then_search(server, bank):
    content = "---\ntitle: Cisco\n---\n\n# Cisco\n\nIOS routers are under attack.\n"
    async with Client(server) as client:
        out = payload(await client.call_tool("write_wiki_page", {
            "path": "security/cisco.md", "content": content, "message": "add cisco page",
        }))
        hits = payload(await client.call_tool("search", {"query": "routers"}))
    assert out["path"] == "wiki/security/cisco.md"
    assert out["warnings"] == []
    assert bank.git("log", "-1", "--format=%s").stdout.strip() == "wiki: add cisco page"
    assert out["commit"] == bank.git("rev-parse", "HEAD").stdout.strip()
    assert [h["path"] for h in hits] == ["wiki/security/cisco.md"]


@pytest.mark.anyio
async def test_write_rejects_raw_and_traversal(server, bank):
    async with Client(server) as client:
        r = await client.call_tool("write_wiki_page", {"path": "raw/x.md", "content": "x", "message": "m"})
        assert "not writable" in error_text(r)
        r = await client.call_tool("write_wiki_page", {"path": "../x.md", "content": "x", "message": "m"})
        assert "not allowed" in error_text(r)
    assert not (bank.root / "x.md").exists()


@pytest.mark.anyio
async def test_write_requires_message_and_size_cap(server):
    async with Client(server) as client:
        r = await client.call_tool("write_wiki_page", {"path": "a.md", "content": "x", "message": " "})
        assert "message is required" in error_text(r)
        big = "x" * (1024 * 1024 + 1)
        r = await client.call_tool("write_wiki_page", {"path": "a.md", "content": big, "message": "m"})
        assert "exceeds" in error_text(r)


@pytest.mark.anyio
async def test_delete_wiki_page(server, bank):
    async with Client(server) as client:
        payload(await client.call_tool("write_wiki_page", {"path": "gone.md", "content": "# Gone\n\nbye\n", "message": "add"}))
        out = payload(await client.call_tool("delete_wiki_page", {"path": "gone.md", "message": "remove"}))
        hits = payload(await client.call_tool("search", {"query": "bye"}))
    assert out["path"] == "wiki/gone.md" and out["commit"]
    assert not (bank.wiki_dir / "gone.md").exists()
    assert hits == []


@pytest.mark.anyio
async def test_read_raw_document_with_markers(server, raw_doc):
    async with Client(server) as client:
        out = payload(await client.call_tool("read", {"path": raw_doc}))
    assert out["path"] == RAW
    assert "<!-- chunk c0 -->" in out["content"]
    assert out["meta"]["title"] == "Foxes"
    assert out["meta"]["date"] == "2026-10-01"
    assert out["next_offset"] is None and out["offset"] == 0


@pytest.mark.anyio
async def test_read_chunk_by_short_and_full_id(server, raw_doc):
    async with Client(server) as client:
        short = payload(await client.call_tool("read", {"path": raw_doc, "chunk": "c0"}))
        full = payload(await client.call_tool("read", {"path": raw_doc, "chunk": f"{RAW}#c0"}))
        missing = await client.call_tool("read", {"path": raw_doc, "chunk": "c9"})
    assert short == full
    assert short["chunk"] == f"{RAW}#c0" and short["heading"] == "Heading"
    assert "foxes" in short["text"]
    assert "no chunk" in error_text(missing)


@pytest.mark.anyio
async def test_read_pages_through_long_documents(server, bank):
    write_raw(bank, RAW, "title: Long", LONG)
    async with Client(server) as client:
        pieces, offset, total = [], 0, None
        while offset is not None:
            out = payload(await client.call_tool("read", {"path": RAW, "offset": offset, "max_chars": 700}))
            pieces.append(out["content"])
            total = out["total_chars"]
            offset = out["next_offset"]
    assert len(pieces) > 2
    assert len("".join(pieces)) == total
    assert "".join(pieces).count("<!-- chunk c") >= 2


@pytest.mark.anyio
async def test_read_errors(server, raw_doc):
    async with Client(server) as client:
        for path in ("raw/none.md", "../etc/passwd", "wiki/../raw/x.md", "/etc/passwd.md"):
            assert error_text(await client.call_tool("read", {"path": path}))


@pytest.mark.anyio
async def test_write_warnings_for_broken_links_and_citations(server, raw_doc):
    content = (
        "# Page\n\nSee [[missing-page]] and [[index]].\n"
        f"Good [@{RAW}#c0] but bad [@{RAW}#c9], [@raw/nope.md] and [@wiki/index.md].\n"
    )
    async with Client(server) as client:
        out = payload(await client.call_tool("write_wiki_page", {"path": "p.md", "content": content, "message": "p"}))
    warnings = out["warnings"]
    assert len(warnings) == 4
    assert any("wiki/missing-page.md" in w for w in warnings)
    assert any(f"{RAW}#c9" in w for w in warnings)
    assert any("raw/nope.md" in w for w in warnings)
    assert any("wiki/index.md" in w for w in warnings)


@pytest.mark.anyio
async def test_ingest_text_dedupes(server, bank):
    args = {"title": "Field notes", "content": "Foxes dig dens.\n", "date": "2026-10-02", "tags": ["animals"]}
    async with Client(server) as client:
        first = payload(await client.call_tool("ingest_text", args))
        second = payload(await client.call_tool("ingest_text", args))
    assert len(first["added"]) == 1 and first["commit"]
    assert first["added"][0].startswith("raw/manual/2026/10/field-notes")
    assert second["added"] == [] and second["skipped"][0]["reason"] == "duplicate"
    assert (bank.root / first["added"][0]).is_file()


@pytest.mark.anyio
async def test_ingest_text_rejects_oversized(server):
    async with Client(server) as client:
        r = await client.call_tool("ingest_text", {"title": "Big", "content": "x" * (5 * 1024 * 1024 + 1)})
    assert "exceeds" in error_text(r)


@pytest.mark.anyio
async def test_log(server, bank):
    async with Client(server) as client:
        out = payload(await client.call_tool("log", {"entry": "integrated foxes"}))
        bad = await client.call_tool("log", {"entry": "two\nlines"})
        long = await client.call_tool("log", {"entry": "x" * 501})
    assert out["commit"]
    assert "integrated foxes" in (bank.wiki_dir / "log.md").read_text()
    assert "single line" in error_text(bad)
    assert "500" in error_text(long)


@pytest.mark.anyio
async def test_list_documents_and_backlinks(server, raw_doc):
    async with Client(server) as client:
        listing = payload(await client.call_tool("list_documents", {"uncited": True}))
        payload(await client.call_tool("write_wiki_page", {
            "path": "animals.md", "content": f"# Animals\n\nFoxes [@{RAW}#c0] and [[index]].\n", "message": "animals",
        }))
        listing_after = payload(await client.call_tool("list_documents", {"uncited": True}))
        links = payload(await client.call_tool("backlinks", {"path": RAW}))
    assert listing["total"] == 1
    assert listing_after["total"] == 0
    assert links == [{"src": "wiki/animals.md", "kind": "cite", "anchor": "c0", "title": "Animals"}]


@pytest.mark.anyio
async def test_lint_reports_broken_link(server, raw_doc):
    async with Client(server) as client:
        payload(await client.call_tool("write_wiki_page", {
            "path": "broken.md", "content": "# B\n\n[[nowhere]]\n", "message": "broken",
        }))
        report = payload(await client.call_tool("lint", {}))
    assert report["counts"]["broken_links"] == 1
    assert report["broken_links"] == [{"src": "wiki/broken.md", "dst": "wiki/nowhere.md"}]
    assert report["counts"]["uncited_sources"] == 1


@pytest.mark.anyio
async def test_status(server, raw_doc):
    async with Client(server) as client:
        report = payload(await client.call_tool("status", {}))
    assert report["stats"]["raw"] == 1
    assert report["stats"]["wiki"] == 2
    assert report["uncited_sources"] == 1
    assert report["recent_commits"][0]["subject"] == "ingest(test): " + RAW
    assert set(report["recent_commits"][0]) == {"sha", "date", "subject"}
