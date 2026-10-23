import hashlib
import stat
from datetime import date, datetime, timezone

import pytest

from tome import markdown as md
from tome.ingest import Source, add_sources, html_to_markdown, pdf_to_text, source_from_file

HTML = """<html><head><title>Cisco patches IOS flaw</title>
<meta name="author" content="Ann Writer"></head>
<body><nav>Home | News | Archive</nav>
<article><h1>Cisco patches IOS flaw</h1>
<p>Cisco has released fixes for a remote code execution vulnerability in IOS XE. Administrators should
upgrade affected routers as soon as possible to close the gap.</p>
<p>The flaw is in the web management interface and can be triggered without authentication by sending a
crafted HTTP request to the device.</p>
<p>Researchers observed scanning activity within hours of the advisory, and several organisations reported
compromised devices in their networks.</p>
<p>Operators who cannot patch immediately should disable the HTTP server and restrict management access to
trusted addresses until an update is installed.</p>
</article><footer>Copyright 2026</footer></body></html>"""


def pdf_with_text(text: str) -> bytes:
    """A one-page PDF with a single Helvetica line, written by hand."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def read_doc(bank, rel):
    meta, body = md.split_frontmatter((bank.root / rel).read_text(encoding="utf-8"))
    return meta, body.strip()


def commit_files(bank, sha=None):
    out = bank.git("show", "--name-only", "--format=%s", sha or "HEAD").stdout.splitlines()
    return out[0], sorted(line for line in out[1:] if line.strip())


def test_frontmatter_fields_and_order(bank, index):
    s = Source(
        title="Hello World", body="\n\nBody text.\n", source="manual", key="manual:1",
        url="https://example.org/a", author="Ann", date="2026-10-20", tags=["a", "b"],
        extra={"nyttig_id": 12, "title": "ignored", "key": "ignored"},
    )
    result = add_sources(bank, index, [s], label="manual")
    assert result.added == ["raw/manual/2026/10/hello-world.md"]
    meta, body = read_doc(bank, result.added[0])
    assert list(meta) == ["title", "source", "url", "author", "date", "tags", "key", "ingested",
                          "content_sha256", "nyttig_id"]
    assert meta["title"] == "Hello World"
    assert meta["date"] == "2026-10-20T00:00:00"
    assert meta["key"] == "manual:1"
    assert meta["nyttig_id"] == 12
    assert meta["content_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert body.startswith("# Hello World\n\nBody text.")


def test_existing_heading_is_kept_and_none_fields_omitted(bank, index):
    s = Source(title="T", body="# Own heading\n\nText", source="manual", date=date(2025, 2, 3))
    path = add_sources(bank, index, [s], label="manual").added[0]
    meta, body = read_doc(bank, path)
    assert path == "raw/manual/2025/02/t.md"
    assert "url" not in meta and "tags" not in meta and "key" not in meta
    assert body.startswith("# Own heading")
    assert "# T" not in body


def test_undated_source_uses_now(bank, index):
    before = datetime.now(timezone.utc).year
    path = add_sources(bank, index, [Source(title="No date", body="x", source="manual")], label="manual").added[0]
    assert path.startswith(f"raw/manual/{before}/")


def test_slug_collision_gets_suffix(bank, index):
    batch = [Source(title="Same", body=f"body {i}", source="manual", date="2026-01-05") for i in range(3)]
    added = add_sources(bank, index, batch, label="manual").added
    assert added == [
        "raw/manual/2026/01/same.md",
        "raw/manual/2026/01/same-2.md",
        "raw/manual/2026/01/same-3.md",
    ]
    # A later batch still avoids files already on disk.
    more = add_sources(bank, index, [Source(title="Same", body="new", source="manual", date="2026-01-05")],
                       label="manual")
    assert more.added == ["raw/manual/2026/01/same-4.md"]


def test_dedupe_by_key_across_batches(bank, index):
    first = add_sources(bank, index, [Source(title="A", body="one", source="nyttig", key="nyttig:item:1",
                                             date="2026-10-01")], label="nyttig")
    second = add_sources(bank, index, [Source(title="A again", body="two", source="nyttig", key="nyttig:item:1")],
                         label="nyttig")
    assert second.added == []
    assert second.skipped == [{"key": "nyttig:item:1", "path": first.added[0], "reason": "duplicate"}]
    assert second.commit is None


def test_dedupe_by_key_within_batch(bank, index):
    batch = [
        Source(title="One", body="a", source="manual", key="k"),
        Source(title="Two", body="b", source="manual", key="k"),
        Source(title="Three", body="c", source="manual"),
    ]
    result = add_sources(bank, index, batch, label="manual")
    assert len(result.added) == 2
    assert result.skipped == [{"key": "k", "path": result.added[0], "reason": "duplicate"}]


def test_empty_body_is_skipped(bank, index):
    result = add_sources(bank, index, [Source(title="Blank", body="  \n ", source="manual")], label="manual")
    assert result.added == []
    assert result.skipped == [{"key": None, "path": None, "reason": "empty"}]
    assert result.commit is None


def test_one_commit_per_batch_with_only_new_files(bank, index):
    add_sources(bank, index, [Source(title="Old", body="x", source="manual", key="old")], label="manual")
    before = bank.git("rev-list", "--count", "HEAD").stdout.strip()
    batch = [
        Source(title="New one", body="a", source="manual", key="n1", date="2026-03-01"),
        Source(title="Old", body="x", source="manual", key="old"),
        Source(title="New two", body="b", source="manual", key="n2", date="2026-03-01"),
    ]
    result = add_sources(bank, index, batch, label="manual")
    assert bank.git("rev-list", "--count", "HEAD").stdout.strip() == str(int(before) + 1)
    subject, names = commit_files(bank, result.commit)
    assert subject == "ingest(manual): 2 source(s)"
    assert names == sorted([*result.added, "wiki/log.md"])
    assert bank.git("status", "--porcelain").stdout == ""
    log = (bank.root / "wiki/log.md").read_text()
    assert "capture(manual): 2 source(s): raw/manual/2026/03/new-one.md, raw/manual/2026/03/new-two.md" in log


def test_log_lists_at_most_five_paths(bank, index):
    batch = [Source(title=f"S{i}", body="x", source="manual", date="2026-04-01") for i in range(7)]
    add_sources(bank, index, batch, label="manual")
    log = (bank.root / "wiki/log.md").read_text().splitlines()[-1]
    assert "7 source(s)" in log and log.endswith("…and 2 more")


def test_no_commit_when_nothing_new(bank, index):
    add_sources(bank, index, [Source(title="A", body="x", source="manual", key="a")], label="manual")
    head = bank.git("rev-parse", "HEAD").stdout
    log = (bank.root / "wiki/log.md").read_text()
    result = add_sources(bank, index, [Source(title="A", body="x", source="manual", key="a")], label="manual")
    assert result.added == [] and result.commit is None
    assert bank.git("rev-parse", "HEAD").stdout == head
    assert (bank.root / "wiki/log.md").read_text() == log


def test_raw_files_are_read_only_and_indexed(bank, index):
    result = add_sources(bank, index, [Source(title="Doc", body="text", source="manual", key="d1")], label="manual")
    path = bank.root / result.added[0]
    assert stat.S_IMODE(path.stat().st_mode) == 0o444
    doc = index.document(result.added[0])
    assert doc["title"] == "Doc" and doc["key"] == "d1"
    assert index.find_key("d1") == result.added[0]


def test_html_to_markdown_extracts_article():
    text, meta = html_to_markdown(HTML, url="https://example.org/cisco")
    assert "remote code execution" in text
    assert "Home | News" not in text
    assert meta["title"] and "Cisco" in meta["title"]
    assert meta["url"] == "https://example.org/cisco"


def test_html_to_markdown_never_raises():
    assert html_to_markdown("") == ("", {})
    text, meta = html_to_markdown(b"\x00\xff not html at all \x80")
    assert isinstance(text, str) and isinstance(meta, dict)


def test_pdf_to_text_blank_page(tmp_path):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    buf = tmp_path / "blank.pdf"
    with buf.open("wb") as f:
        writer.write(f)
    text, meta = pdf_to_text(buf.read_bytes())
    assert text == ""
    assert meta.get("title") in (None, "")


def test_pdf_to_text_extracts_text():
    text, meta = pdf_to_text(pdf_with_text("Hello from a PDF"))
    assert "Hello from a PDF" in text


def test_pdf_to_text_bad_input():
    assert pdf_to_text(b"not a pdf") == ("", {})


def test_source_from_markdown_with_frontmatter(tmp_path):
    path = tmp_path / "post.md"
    path.write_text("---\ntitle: From FM\nurl: https://x.test/p\nauthor: Bo\ndate: 2025-06-01\n"
                    "tags: [one, two]\nkey: ext:42\nnyttig_id: 7\n---\n\n# Heading\n\nBody.\n")
    src = source_from_file(path, source="inbox", tags=["extra"])
    assert src.title == "From FM"
    assert (src.url, src.author, src.key) == ("https://x.test/p", "Bo", "ext:42")
    assert src.date == date(2025, 6, 1)
    assert src.tags == ["one", "two", "extra"]
    assert src.extra == {"nyttig_id": 7}
    assert src.body.startswith("# Heading")


def test_source_from_text_uses_stem_and_content_key(tmp_path):
    path = tmp_path / "my-note_v2.txt"
    path.write_bytes(b"plain words\n")
    src = source_from_file(path, source="inbox")
    assert src.title == "my note v2"
    assert src.body == "plain words\n"
    assert src.key == "inbox:sha256:" + hashlib.sha256(b"plain words\n").hexdigest()


def test_source_from_html(tmp_path):
    path = tmp_path / "page.html"
    path.write_text(HTML)
    src = source_from_file(path, source="inbox")
    assert "Cisco" in src.title
    assert "remote code execution" in src.body
    assert src.author == "Ann Writer"
    assert src.key.startswith("inbox:sha256:")


def test_source_from_unknown_suffix_is_none(tmp_path):
    path = tmp_path / "file.docx"
    path.write_bytes(b"PK")
    assert source_from_file(path, source="inbox") is None


def test_failed_batch_leaves_raw_clean(bank, index, monkeypatch):
    from tome import ingest as ingest_mod
    calls = {"n": 0}
    real = bank.create_raw

    def flaky(rel, content):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real(rel, content)

    monkeypatch.setattr(bank, "create_raw", flaky)
    sources = [ingest_mod.Source(title=f"T{i}", body="text", source="manual", key=f"k{i}") for i in range(3)]
    with pytest.raises(OSError):
        ingest_mod.add_sources(bank, index, sources, label="manual")
    assert bank.raw_changes() == []


def test_source_from_file_size_limit(tmp_path, monkeypatch):
    from tome import ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "MAX_FILE_BYTES", 10)
    big = tmp_path / "big.txt"
    big.write_text("x" * 11)
    with pytest.raises(ValueError):
        ingest_mod.source_from_file(big, source="manual")
    assert ingest_mod.source_from_file(tmp_path / "unknown.bin", source="manual") is None
