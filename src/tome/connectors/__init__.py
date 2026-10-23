"""Connectors turn an outside system into Sources.

Every connector yields Sources; capture always goes through `ingest.add_sources`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from ..ingest import Source


class Connector(Protocol):
    name: str

    def fetch(self, **params) -> Iterable[Source]: ...
