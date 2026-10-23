import pytest

from tome.bank import Bank
from tome.index import Index


@pytest.fixture
def bank(tmp_path) -> Bank:
    return Bank.init(tmp_path / "bank")


@pytest.fixture
def index(bank) -> Index:
    return Index(bank, min_sync_interval=0)


def write_raw(bank: Bank, rel: str, meta: str, body: str) -> str:
    """Create a raw document the way capture does (create-only, committed)."""
    with bank.lock():
        path = bank.create_raw(rel, f"---\n{meta.strip()}\n---\n\n{body}")
        bank.commit(f"ingest(test): {rel}", [path])
    return path
