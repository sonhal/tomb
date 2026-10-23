from tome import markdown as md


def test_frontmatter_roundtrip():
    text = md.render({"title": "Ærlig talt", "tags": ["a", "b"]}, "# Body\n\ntext\n")
    meta, body = md.split_frontmatter(text)
    assert meta == {"title": "Ærlig talt", "tags": ["a", "b"]}
    assert body.lstrip().startswith("# Body")


def test_frontmatter_missing_or_invalid():
    assert md.split_frontmatter("no frontmatter") == ({}, "no frontmatter")
    assert md.split_frontmatter("---\n: [\n---\nbody")[0] == {}
    assert md.split_frontmatter("---\n- a list\n---\nbody")[0] == {}


def test_to_iso():
    from datetime import date
    assert md.to_iso(date(2026, 1, 2)) == "2026-01-02"
    assert md.to_iso("2026-01-02T10:00:00Z") == "2026-01-02T10:00:00+00:00"
    assert md.to_iso("") is None


def test_slugify():
    assert md.slugify("Sårbarhet i Cisco IOS — CVE-2026-1234!") == "sarbarhet-i-cisco-ios-cve-2026-1234"
    assert md.slugify("???") == "untitled"
    assert len(md.slugify("x" * 200)) == 60


def test_links_and_citations():
    body = (
        "See [[sqlite]] and [[topics/search|search]] and [[wiki/fts.md#ranking]].\n"
        "Claim [@raw/nyttig/2026/10/a.md#c3], whole [@raw/manual/b], dup [@raw/nyttig/2026/10/a.md#c3].\n"
    )
    assert md.wikilinks(body) == ["wiki/sqlite.md", "wiki/topics/search.md", "wiki/fts.md"]
    assert md.citations(body) == [("raw/nyttig/2026/10/a.md", "c3"), ("raw/manual/b.md", None)]


def test_chunks_follow_headings_and_skip_fenced_headings():
    body = "intro text\n\n# A\n\none\n\n## B\n\n```\n# not a heading\n```\n\ntwo\n\n# C\n\nthree\n"
    chunks = md.chunk(body)
    assert [c.heading for c in chunks] == ["", "A", "A > B", "C"]
    assert "# not a heading" in chunks[2].text
    assert [c.anchor for c in chunks] == ["c0", "c1", "c2", "c3"]
    assert md.first_heading(body) == "A"


def test_chunk_lines_point_at_source():
    body = "# A\n\nfirst para\n\nsecond para\n"
    (c,) = md.chunk(body)
    assert body.splitlines()[c.line - 1] == "first para"


def test_long_sections_split_with_overlap():
    para = " ".join(f"w{i}." for i in range(300))
    body = "# Long\n\n" + "\n\n".join([para] * 3)
    chunks = md.chunk(body, target_words=350, max_words=500, overlap_words=50)
    assert len(chunks) == 3
    assert all(c.heading == "Long" for c in chunks)
    # Each later chunk starts with the tail of the previous one.
    assert chunks[1].text.startswith("w250.")
    assert len(chunks[1].text.split()) == 350


def test_oversized_paragraph_is_split():
    body = " ".join(f"word{i}" for i in range(1200))
    chunks = md.chunk(body, target_words=350, max_words=500, overlap_words=0)
    assert all(len(c.text.split()) <= 500 for c in chunks)
    assert sum(len(c.text.split()) for c in chunks) == 1200
