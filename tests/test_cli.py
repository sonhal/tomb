import json

import pytest

from tome.bank import Bank
from tome.cli import main


@pytest.fixture(autouse=True)
def no_env_home(monkeypatch):
    monkeypatch.delenv("TOME_HOME", raising=False)


@pytest.fixture
def home(tmp_path):
    return str(Bank.init(tmp_path / "bank").root)


@pytest.fixture
def note(tmp_path):
    path = tmp_path / "fox-notes.md"
    path.write_text("---\ntitle: Fox notes\ntags: [animals]\n---\n\n# Dens\n\nFoxes dig dens under hedges.\n")
    return path


def test_init_creates_bank(tmp_path, capsys):
    target = tmp_path / "new"
    assert main(["init", str(target)]) == 0
    assert capsys.readouterr().out.strip() == str(target.resolve())
    assert (target / "TOME.md").is_file()


def test_init_uses_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TOME_HOME", str(tmp_path / "env-bank"))
    assert main(["init"]) == 0
    assert (tmp_path / "env-bank" / "TOME.md").is_file()


def test_ingest_file_then_search(home, note, capsys):
    assert main(["--home", home, "ingest", "file", str(note), "--tag", "wild"]) == 0
    out = capsys.readouterr().out
    assert "added 1" in out and "raw/manual/" in out

    assert main(["--home", home, "search", "foxes", "dens"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "#c0" in lines[0] and "Fox notes" in lines[0] and "Dens" in lines[0]
    assert lines[1].startswith(" ") and "dig" in lines[1]


def test_search_json_and_filters(home, note, capsys):
    main(["--home", home, "ingest", "file", str(note), "--tag", "wild", "--source", "field"])
    capsys.readouterr()
    assert main(["--home", home, "search", "foxes", "--json", "--tag", "wild", "--source", "field"]) == 0
    results = json.loads(capsys.readouterr().out)
    assert len(results) == 1 and results[0]["source"] == "field"
    assert main(["--home", home, "search", "foxes", "--tag", "absent"]) == 0
    assert "no results" in capsys.readouterr().out


def test_ingest_file_reports_unsupported(home, tmp_path, capsys):
    binary = tmp_path / "photo.png"
    binary.write_bytes(b"\x89PNG")
    assert main(["--home", home, "ingest", "file", str(binary)]) == 0
    captured = capsys.readouterr()
    assert "unsupported file" in captured.err and "added 0" in captured.out


def test_ingest_inbox(home, capsys):
    inbox = Bank(home).inbox_dir
    inbox.mkdir(exist_ok=True)
    (inbox / "drop.txt").write_text("Dropped note about owls.\n")
    assert main(["--home", home, "ingest", "inbox", "--min-age", "0"]) == 0
    assert "added 1" in capsys.readouterr().out
    assert not (inbox / "drop.txt").exists()


def test_lint_exit_codes(home, capsys):
    assert main(["--home", home, "lint"]) == 0
    capsys.readouterr()
    page = Bank(home).wiki_dir / "broken.md"
    page.write_text("# Broken\n\nSee [[nowhere]].\n")
    assert main(["--home", home, "lint"]) == 1
    assert "broken_links" in capsys.readouterr().out
    assert main(["--home", home, "lint", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["counts"]["broken_links"] == 1


def test_lint_fails_on_raw_tampering(home, capsys):
    (Bank(home).raw_dir / "stray.md").write_text("added by hand")
    assert main(["--home", home, "lint"]) == 1
    assert "raw_tampering" in capsys.readouterr().out


def test_reindex_full_rebuilds_database(home, note, capsys):
    main(["--home", home, "ingest", "file", str(note)])
    capsys.readouterr()
    bank = Bank(home)
    assert main(["--home", home, "reindex", "--full"]) == 0
    assert "added 3" in capsys.readouterr().out
    assert bank.db_path.is_file()


def test_status_prints_json(home, note, capsys):
    main(["--home", home, "ingest", "file", str(note)])
    capsys.readouterr()
    assert main(["--home", home, "status"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["stats"]["raw"] == 1
    assert report["uncited_sources"] == 1
    assert report["recent_commits"][0]["subject"].startswith("ingest(manual)")


def test_missing_home_is_an_error(capsys):
    assert main(["search", "foxes"]) == 2
    assert capsys.readouterr().err.startswith("tome: no bank given")


def test_uninitialized_home_is_an_error(tmp_path, capsys):
    assert main(["--home", str(tmp_path / "nothing"), "status"]) == 2
    assert "run `tome init`" in capsys.readouterr().err
