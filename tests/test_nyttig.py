import base64
import gzip
import json
import re
from datetime import datetime, timezone

import anyio
import httpx
import pytest
from mcp import Client

from tome import markdown as md
from tome.bank import Bank
from tome.cli import main
from tome.config import Settings
from tome.connectors import nyttig
from tome.connectors.nyttig import NyttigClient, NyttigConnector, NyttigError, ingest_nyttig, ingest_nyttig_digests
from tome.server import create_server

from .test_ingest import HTML, pdf_with_text

PDF_TYPE = "application/pdf"
HTML_TYPE = "text/html; charset=utf-8"


class FakeNyttig:
    """nyttig-api over httpx.MockTransport, in nyttig's protojson shapes: ids are
    strings, zero values and empty lists are omitted, an empty page has no "items"."""

    def __init__(self):
        self.items: list[dict] = []
        self.archives: dict[str, dict] = {}
        self.files: dict[str, bytes] = {}
        self.failing: set[str] = set()  # archive ids whose file download fails
        self.sources = [{"id": "1", "name": "Krebs"}, {"id": "2", "name": "Hacker News"}]
        self.tags = [{"id": "3", "name": "Security"}]
        self.assessors = [{"id": "7", "name": "claude"}]
        self.series = [{"id": "3", "assessor_id": "7", "assessor_name": "claude", "name": "Daily"}]
        self.digests: list[dict] = []
        self.requests: list[httpx.Request] = []

    def client(self, user: str | None = "owner", password: str | None = "pw") -> NyttigClient:
        return NyttigClient(
            "https://news.example.org", user=user, password=password,
            transport=httpx.MockTransport(self.handle),
        )

    def add_item(
        self, id: str, title: str, *, status: str | None = "done", content: bytes = HTML.encode(),
        content_type: str = HTML_TYPE, description: str = "", tags=(), assessments=(),
    ) -> dict:
        item = {
            "id": id, "source_id": "1", "source_name": "Krebs", "guid": f"g{id}",
            "link": f"https://krebs.example.org/{id}", "title": title, "author": "Brian K",
            "published": "2026-10-20T08:00:00.123456789Z", "fetched_at": "2026-10-20T08:05:00Z",
            "description": description, "tags": list(tags), "assessments": list(assessments),
        }
        if status is not None:
            archive_id = str(100 + int(id))
            item["archive"] = {"id": archive_id, "status": status}
            self.archives[archive_id] = {
                "id": archive_id, "item_id": id, "link": item["link"], "title": title,
                "source_name": "Krebs", "status": status, "content_type": content_type,
                "final_url": item["link"] + "?utm=1",
            }
            self.files[archive_id] = content
        self.items.append(item)
        return item

    def add_digest(self, id: str, title: str, *, updated: str = "2026-10-21T09:00:00Z", body: str = "Body.", **extra):
        digest = {
            "id": id, "series_id": "3", "series_name": "Daily", "assessor_id": "7", "assessor_name": "claude",
            "title": title, "body": body, "period_start": "2026-10-20T00:00:00Z",
            "period_end": "2026-10-21T00:00:00Z", "created_at": "2026-10-21T08:00:00Z", "updated_at": updated,
            "items": [], "inputs": [],
        }
        digest.update(extra)
        self.digests.append(digest)
        return digest

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, q = request.url.path, request.url.params
        if path == "/api/items":
            return _json(self._items(q))
        if path == "/api/sources":
            return _json(_wrap("sources", self.sources))
        if path == "/api/tags":
            return _json(_wrap("tags", self.tags))
        if path == "/api/assessors":
            return _json(_wrap("assessors", self.assessors))
        if path == "/api/digest-series":
            rows = [s for s in self.series if "assessor" not in q or s["assessor_id"] == q["assessor"]]
            return _json(_wrap("series", rows))
        if path == "/api/digests":
            return _json(self._digests(q))
        if m := re.fullmatch(r"/api/digests/(\d+)", path):
            found = [d for d in self.digests if d["id"] == m.group(1)]
            return _json(found[0]) if found else _error(404, f"digest {m.group(1)} not found")
        if m := re.fullmatch(r"/api/archives/(\d+)(/file)?", path):
            aid = m.group(1)
            if aid not in self.archives:
                return _error(404, f"archive {aid} not found")
            if not m.group(2):
                return _json(self.archives[aid])
            if aid in self.failing:
                return _error(502, "archive file is gone")
            return httpx.Response(200, content=self.files[aid], headers={"Content-Type": "application/octet-stream"})
        return _error(404, "not found")

    def _items(self, q) -> dict:
        rows = list(reversed(self.items))  # newest first
        if "source" in q:
            rows = [i for i in rows if i["source_id"] == q["source"]]
        if "tag" in q:
            rows = [i for i in rows if any(t["id"] == q["tag"] for t in i["tags"])]
        offset, limit = int(q.get("offset", 0)), int(q.get("limit", 100))
        return _wrap("items", rows[offset:offset + limit], total=len(rows))

    def _digests(self, q) -> dict:
        rows = sorted((d for d in self.digests if d["series_id"] == q["series"]), key=lambda d: int(d["id"]), reverse=True)
        if "before" in q:
            rows = [d for d in rows if int(d["id"]) < int(q["before"])]
        limit = int(q.get("limit", 20))
        page = rows[:limit]
        if q.get("body") != "1":
            page = [{k: v for k, v in d.items() if k != "body"} for d in page]
        body = _wrap("digests", page)
        if len(rows) > limit:
            body["has_more"] = True
        return body


def _wrap(key: str, rows: list, **extra) -> dict:
    """protojson leaves out empty repeated fields and zero totals."""
    body = {key: rows} if rows else {}
    body.update({k: v for k, v in extra.items() if v})
    return body


def _json(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def _error(status: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": message})


@pytest.fixture
def fake() -> FakeNyttig:
    return FakeNyttig()


def last_items_request(fake: FakeNyttig) -> httpx.QueryParams:
    return [r for r in fake.requests if r.url.path == "/api/items"][-1].url.params


def raw_text(bank, rel: str) -> tuple[dict, str]:
    meta, body = md.split_frontmatter((bank.root / rel).read_text(encoding="utf-8"))
    return meta, body


# ── client ──────────────────────────────────────────────────


def test_client_from_settings_needs_a_url(tmp_path):
    assert NyttigClient.from_settings(Settings(home=tmp_path)) is None
    client = NyttigClient.from_settings(Settings(home=tmp_path, nyttig_url="https://news.example.org"))
    assert isinstance(client, NyttigClient)


def test_basic_auth_is_sent(fake):
    fake.client().sources()
    expected = "Basic " + base64.b64encode(b"owner:pw").decode()
    assert fake.requests[0].headers["Authorization"] == expected


def test_error_message_comes_from_nyttig(fake):
    with pytest.raises(NyttigError, match="archive 9 not found"):
        fake.client().archive("9")


def test_error_without_json_body_names_the_status():
    transport = httpx.MockTransport(lambda request: httpx.Response(502, text="<html>bad gateway</html>"))
    client = NyttigClient("https://news.example.org", transport=transport)
    with pytest.raises(NyttigError, match="HTTP 502"):
        client.items()


def test_unreachable_is_a_nyttig_error():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    client = NyttigClient("https://news.example.org", transport=httpx.MockTransport(refuse))
    with pytest.raises(NyttigError, match="unreachable"):
        client.sources()


def test_pages_keep_the_time_window_fixed(fake, monkeypatch):
    monkeypatch.setattr(nyttig, "MAX_PAGE", 2)
    for n in range(1, 6):
        fake.add_item(str(n), f"Note {n}", status=None)
    got = list(fake.client().iter_items({"after": "1790000000"}, limit=10))
    assert [i["id"] for i in got] == ["5", "4", "3", "2", "1"]  # newest first
    assert [(r.url.params["offset"], r.url.params["limit"]) for r in fake.requests] == [("0", "2"), ("2", "2"), ("4", "2")]
    assert {r.url.params["after"] for r in fake.requests} == {"1790000000"}


def test_limit_stops_paging_and_empty_page_ends_cleanly(fake, monkeypatch):
    monkeypatch.setattr(nyttig, "MAX_PAGE", 2)
    for n in range(1, 4):
        fake.add_item(str(n), f"Note {n}", status=None)
    assert len(list(fake.client().iter_items({}, limit=3))) == 3
    assert len(fake.requests) == 2
    fake.requests.clear()
    assert list(fake.client().iter_items({"source": "99"}, limit=5)) == []
    assert len(fake.requests) == 1


# ── names and windows ───────────────────────────────────────


def test_names_resolve_to_ids_case_insensitively(bank, index, fake):
    fake.add_item("1", "Cisco patches IOS flaw", status=None, tags=[{"id": "3", "name": "Security"}])
    fake.add_item("2", "Other", status=None)
    ingest_nyttig(bank, index, fake.client(), source="krebs", tag="SECURITY")
    params = last_items_request(fake)
    assert params["source"] == "1" and params["tag"] == "3"
    ingest_nyttig(bank, index, fake.client(), source="2")
    assert last_items_request(fake)["source"] == "2"


def test_unknown_name_lists_valid_names(fake):
    with pytest.raises(ValueError, match="unknown tag 'nope'; valid: Security"):
        list(NyttigConnector(fake.client()).fetch(tag="nope"))
    assert not any(r.url.path == "/api/items" for r in fake.requests)


def test_since_becomes_unix_seconds(fake):
    list(NyttigConnector(fake.client()).fetch(since="2026-10-01"))
    expected = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
    assert last_items_request(fake)["after"] == str(expected)


def test_bad_since_is_a_value_error(fake):
    with pytest.raises(ValueError, match="bad since"):
        list(NyttigConnector(fake.client()).fetch(since="last tuesday"))


# ── items ───────────────────────────────────────────────────


def test_archived_html_item_becomes_raw_document(bank, index, fake):
    fake.add_item(
        "1", "Cisco patches IOS flaw", tags=[{"id": "3", "name": "Security"}],
        assessments=[
            {"id": "9", "item_id": "1", "assessor_id": "7", "assessor_name": "claude", "score": 0.9},
            {"id": "10", "item_id": "1", "assessor_id": "7", "assessor_name": "claude", "tag_id": "3", "score": 0.2},
            {"id": "11", "item_id": "1", "assessor_id": "8", "assessor_name": "human", "score": 0},
        ],
    )
    result = ingest_nyttig(bank, index, fake.client())
    assert result["added"] == ["raw/nyttig/2026/10/cisco-patches-ios-flaw.md"]
    meta, body = raw_text(bank, result["added"][0])
    assert meta["title"] == "Cisco patches IOS flaw"
    assert meta["source"] == "nyttig" and meta["key"] == "nyttig:item:1"
    assert meta["url"] == "https://krebs.example.org/1"
    assert meta["author"] == "Brian K"
    assert meta["date"].startswith("2026-10-20T08:00:00")
    assert meta["tags"] == ["Security"]
    assert meta["nyttig_item_id"] == "1" and meta["nyttig_archive_id"] == "101"
    assert meta["nyttig_source"] == "Krebs" and meta["content"] == "archive"
    assert meta["scores"] == {"claude": 0.9, "human": 0}
    assert "remote code execution" in body and "Home | News" not in body
    assert fake.requests[-1].url.path == "/api/archives/101/file"


def test_pdf_archive_is_converted_to_text(bank, index, fake):
    fake.add_item("2", "Annual report", content=pdf_with_text("Quarterly figures"), content_type=PDF_TYPE)
    result = ingest_nyttig(bank, index, fake.client())
    _, body = raw_text(bank, result["added"][0])
    assert "Quarterly figures" in body


def test_gzipped_archive_is_decompressed(bank, index, fake):
    fake.add_item("3", "Compressed", content=gzip.compress(HTML.encode()))
    result = ingest_nyttig(bank, index, fake.client())
    _, body = raw_text(bank, result["added"][0])
    assert "remote code execution" in body


def test_unarchived_items_are_skipped_and_reported(bank, index, fake):
    fake.add_item("1", "Done", status="done")
    fake.add_item("2", "Queued", status="pending")
    fake.add_item("3", "Working", status="running")
    fake.add_item("4", "Broken", status="failed")
    fake.add_item("5", "Never", status=None)
    result = ingest_nyttig(bank, index, fake.client(), limit=10)
    assert len(result["added"]) == 1
    assert result["not_archived_count"] == 4
    statuses = {r["id"]: r["archive_status"] for r in result["not_archived"]}
    assert statuses == {"2": "pending", "3": "running", "4": "failed", "5": "never"}
    assert result["not_archived"][0]["link"].startswith("https://krebs.example.org/")
    files = [r.url.path for r in fake.requests if r.url.path.endswith("/file")]
    assert files == ["/api/archives/101/file"]


def test_include_unarchived_uses_the_description(bank, index, fake):
    long_html = (
        "<p>Short teaser about <b>routers</b> and switches. Operators should patch quickly and review "
        "management access across the estate.</p><p>A second paragraph explains who is affected.</p>"
    )
    fake.add_item("6", "Teaser", status=None, description=long_html)
    fake.add_item("7", "Plain", status=None, description="Plain text teaser.")
    fake.add_item("8", "Empty", status=None, description="")
    fake.add_item("9", "Tiny", status=None, description="<b>Tiny</b> note")
    result = ingest_nyttig(bank, index, fake.client(), include_unarchived=True)
    assert len(result["added"]) == 3
    assert result["not_archived_count"] == 1 and result["not_archived"][0]["id"] == "8"
    by_title = {raw_text(bank, rel)[0]["title"]: rel for rel in result["added"]}
    meta, body = raw_text(bank, by_title["Teaser"])
    assert meta["content"] == "description"
    assert "**routers**" in body and "<b>" not in body
    assert raw_text(bank, by_title["Plain"])[1].strip().startswith("# Plain")
    assert "<b>Tiny</b> note" in raw_text(bank, by_title["Tiny"])[1]  # too short to extract: raw text


def test_failed_item_does_not_abort_the_run(bank, index, fake):
    fake.add_item("1", "Gone")
    fake.failing.add("101")
    fake.add_item("2", "Fine")
    result = ingest_nyttig(bank, index, fake.client())
    assert [f["id"] for f in result["failed"]] == ["1"]
    assert "archive file is gone" in result["failed"][0]["error"]
    assert len(result["added"]) == 1


def test_known_items_are_skipped_without_archive_requests(bank, index, fake):
    fake.add_item("1", "Cisco patches IOS flaw")
    fake.add_item("2", "Unarchived", status=None)
    first = ingest_nyttig(bank, index, fake.client())
    assert len(first["added"]) == 1
    fake.requests.clear()
    second = ingest_nyttig(bank, index, fake.client())
    assert second["added"] == [] and second["commit"] is None
    assert second["known"] == 1
    assert not any(r.url.path.startswith("/api/archives") for r in fake.requests)


def test_connector_report_lists_known_keys(bank, index, fake):
    fake.add_item("1", "Known")
    connector = NyttigConnector(fake.client(), known=lambda key: key == "nyttig:item:1")
    assert list(connector.fetch()) == []
    assert connector.report["known"] == ["nyttig:item:1"]
    assert not any(r.url.path.startswith("/api/archives") for r in fake.requests)


# ── digests ─────────────────────────────────────────────────


def test_digests_by_assessor_and_series_name(bank, index, fake):
    fake.add_digest("40", "Monday", items=[{"item_id": "11", "title": "A", "link": "x", "source_name": "Krebs"}])
    fake.add_digest("41", "Tuesday", inputs=[{"id": "40", "title": "Monday", "series_name": "Daily"}])
    result = ingest_nyttig_digests(bank, index, fake.client(), "claude/daily")
    assert len(result["added"]) == 2
    # newest first: Tuesday (41) then Monday (40)
    meta_tuesday, _ = raw_text(bank, result["added"][0])
    assert meta_tuesday["input_digest_ids"] == ["40"]
    meta_monday, body = raw_text(bank, result["added"][1])
    assert body.strip().startswith("# Monday")
    assert meta_monday["tags"] == ["digest"] and meta_monday["source"] == "nyttig"
    assert meta_monday["nyttig_digest_id"] == "40" and meta_monday["series"] == "claude/Daily"
    assert meta_monday["item_ids"] == ["11"]
    assert meta_monday["key"] == "nyttig:digest:40:2026-10-21T09:00:00Z"
    assert meta_monday["period_end"].startswith("2026-10-21")


def test_series_by_id_and_unknown_series(bank, index, fake):
    fake.add_digest("40", "Monday")
    assert len(ingest_nyttig_digests(bank, index, fake.client(), "3")["added"]) == 1
    with pytest.raises(ValueError, match="unknown series 'Weekly' for assessor 'claude'; valid: Daily"):
        ingest_nyttig_digests(bank, index, fake.client(), "claude/Weekly")
    with pytest.raises(ValueError, match="series must be an id or ASSESSOR/NAME"):
        ingest_nyttig_digests(bank, index, fake.client(), "daily")


def test_edited_digest_is_captured_again(bank, index, fake):
    fake.add_digest("40", "Monday")
    fake.add_digest("41", "Tuesday")
    assert len(ingest_nyttig_digests(bank, index, fake.client(), "3")["added"]) == 2
    fake.digests[1]["updated_at"] = "2026-10-22T10:00:00Z"
    fake.digests[1]["body"] = "Revised body."
    fake.requests.clear()
    again = ingest_nyttig_digests(bank, index, fake.client(), "3")
    assert again["known"] == 1 and len(again["added"]) == 1
    meta, body = raw_text(bank, again["added"][0])
    assert meta["key"] == "nyttig:digest:41:2026-10-22T10:00:00Z"
    assert "Revised body." in body
    details = [r.url.path for r in fake.requests if r.url.path.startswith("/api/digests/")]
    assert details == ["/api/digests/41"]


def test_digest_pages_follow_has_more(bank, index, fake, monkeypatch):
    monkeypatch.setattr(nyttig, "MAX_DIGEST_PAGE", 1)
    for n in (40, 41, 42):
        fake.add_digest(str(n), f"Day {n}")
    result = ingest_nyttig_digests(bank, index, fake.client(), "3", limit=3)
    assert len(result["added"]) == 3
    befores = [r.url.params.get("before") for r in fake.requests if r.url.path == "/api/digests"]
    assert befores == [None, "42", "41"]


def test_digest_listing_is_cheap_on_rerun(bank, index, fake):
    fake.add_digest("40", "Monday")
    ingest_nyttig_digests(bank, index, fake.client(), "3")
    fake.requests.clear()
    again = ingest_nyttig_digests(bank, index, fake.client(), "3")
    assert again["added"] == [] and again["commit"] is None and again["known"] == 1
    assert not any(r.url.path == "/api/digests/40" for r in fake.requests)


# ── MCP tools and CLI ───────────────────────────────────────


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_mcp_tools_only_with_a_client(bank, index, fake):
    async def names(server):
        async with Client(server) as client:
            return {t.name for t in (await client.list_tools()).tools}

    base = anyio.run(names, create_server(bank, index))
    assert "ingest_nyttig" not in base and "ingest_nyttig_digests" not in base
    with_client = anyio.run(names, create_server(bank, index, fake.client()))
    assert {"ingest_nyttig", "ingest_nyttig_digests"} <= with_client


@pytest.mark.anyio
async def test_mcp_ingest_reports_nyttig_errors_as_tool_errors(bank, index, fake):
    server = create_server(bank, index, fake.client())
    async with Client(server) as client:
        result = await client.call_tool("ingest_nyttig", {"tag": "nope"})
    assert result.is_error
    assert "unknown tag 'nope'" in result.content[0].text


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("TOME_HOME", raising=False)
    return str(Bank.init(tmp_path / "bank").root)


def test_cli_requires_nyttig_url(home, monkeypatch, capsys):
    monkeypatch.delenv("TOME_NYTTIG_URL", raising=False)
    assert main(["--home", home, "ingest", "nyttig"]) == 2
    assert capsys.readouterr().err.strip() == "tome: TOME_NYTTIG_URL is not set"
    assert main(["--home", home, "ingest", "nyttig-digests", "claude/daily"]) == 2


def test_cli_ingests_with_the_configured_client(home, fake, monkeypatch, capsys):
    fake.add_item("1", "Cisco patches IOS flaw")
    monkeypatch.setattr(NyttigClient, "from_settings", classmethod(lambda cls, settings: fake.client()))
    assert main(["--home", home, "ingest", "nyttig", "--since", "7d"]) == 0
    out = capsys.readouterr().out
    assert "added 1" in out and "raw/nyttig/" in out and "known 0, not archived 0, failed 0" in out
    assert main(["--home", home, "ingest", "nyttig", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["added"] == [] and result["known"] == 1
