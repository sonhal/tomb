"""nyttig connector: archived items and digests from nyttig-api, read over HTTP.

Only GETs. Items are captured from their saved archives (HTML or PDF); digests
from their Markdown bodies. Everything nyttig returns is untrusted text.
"""

from __future__ import annotations

import gzip
import re
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any

import httpx

from ..bank import Bank
from ..config import Settings
from ..index import Index, parse_when
from ..ingest import Source, add_sources, html_to_markdown, pdf_to_text

MAX_PAGE = 500  # nyttig's maxSearchLimit for /api/items
MAX_DIGEST_PAGE = 100
REPORT_CAP = 50
_HTML_TAG = re.compile(r"<[a-zA-Z/!][^>]*>")
_FRACTION = re.compile(r"(\.\d{6})\d+")  # nyttig sends nanoseconds; Python takes microseconds


class NyttigError(RuntimeError):
    """nyttig refused a request or could not be reached."""


class NyttigClient:
    def __init__(
        self,
        base_url: str,
        *,
        user: str | None = None,
        password: str | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        auth = httpx.BasicAuth(user or "", password or "") if (user or password) else None
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/", auth=auth, timeout=timeout, transport=transport,
            headers={"Accept": "application/json"},
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> NyttigClient | None:
        if not settings.nyttig_url:
            return None
        return cls(settings.nyttig_url, user=settings.nyttig_user, password=settings.nyttig_password)

    def items(self, **params: Any) -> dict[str, Any]:
        return self._json("api/items", **params)

    def iter_items(self, params: dict[str, Any], limit: int) -> Iterator[dict[str, Any]]:
        """Items matching params, page by page, up to limit. Keep `after` in params
        so the time window stays fixed while pages are read."""
        offset = 0
        while offset < limit:
            size = min(MAX_PAGE, limit - offset)
            body = self.items(**params, limit=size, offset=offset)
            rows = body.get("items", [])
            yield from rows
            offset += len(rows)
            if len(rows) < size or offset >= body.get("total", 0):
                return

    def archive(self, archive_id: str) -> dict[str, Any]:
        return self._json(f"api/archives/{archive_id}")

    def archive_file(self, archive_id: str) -> bytes:
        return self._get(f"api/archives/{archive_id}/file").content

    def sources(self) -> dict[str, Any]:
        return self._json("api/sources")

    def tags(self) -> dict[str, Any]:
        return self._json("api/tags")

    def assessors(self) -> dict[str, Any]:
        return self._json("api/assessors")

    def digest_series(self, assessor_id: str | None = None) -> dict[str, Any]:
        return self._json("api/digest-series", assessor=assessor_id)

    def digests(
        self, series_id: str, limit: int, body: bool = True, before: str | None = None,
    ) -> dict[str, Any]:
        return self._json(
            "api/digests", series=series_id, limit=limit, before=before, body="1" if body else "0",
        )

    def digest(self, digest_id: str) -> dict[str, Any]:
        return self._json(f"api/digests/{digest_id}")

    def _json(self, path: str, **params: Any) -> dict[str, Any]:
        return self._get(path, **params).json()

    def _get(self, path: str, **params: Any) -> httpx.Response:
        query = {k: v for k, v in params.items() if v is not None}
        try:
            response = self._http.get(path, params=query)
        except httpx.HTTPError as e:
            raise NyttigError(f"nyttig unreachable: {e}") from e
        if response.is_error:
            raise NyttigError(_error_message(response))
        return response


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    message = body.get("error") if isinstance(body, dict) else None
    return str(message) if message else f"nyttig returned HTTP {response.status_code}"


class NyttigConnector:
    name = "nyttig"

    def __init__(self, client: NyttigClient, *, known: Callable[[str], bool] = lambda key: False):
        self.client = client
        self.known = known  # keys already captured: skipped without any download
        self.report: dict[str, list[Any]] = {"known": [], "not_archived": [], "failed": []}

    def fetch(
        self,
        *,
        view: str | None = None,
        query: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        since: str | None = None,
        assessor: str | None = None,
        min_score: float | None = None,
        limit: int = 50,
        include_unarchived: bool = False,
    ) -> Iterator[Source]:
        params = {
            "view": view,
            "q": query,
            "source": self._ref(source, self.client.sources, "sources", "source"),
            "tag": self._ref(tag, self.client.tags, "tags", "tag"),
            "assessor": self._ref(assessor, self.client.assessors, "assessors", "assessor"),
            "min_score": min_score,
            "after": _after(since),
        }
        params = {k: v for k, v in params.items() if v is not None}
        for item in self.client.iter_items(params, limit):
            key = f"nyttig:item:{item['id']}"
            if self.known(key):
                self.report["known"].append(key)
                continue
            try:
                src = self._item_source(item, key, include_unarchived)
            except (NyttigError, httpx.HTTPError, OSError, ValueError) as e:
                self.report["failed"].append({"id": item["id"], "title": item.get("title", ""), "error": str(e)})
                continue
            if src is not None:
                yield src

    def fetch_digests(self, series: str, *, limit: int = 10) -> Iterator[Source]:
        """Digests of one series, newest first. An edited digest has a new key."""
        series_id = self._series_id(series)
        remaining = limit
        before = None
        while remaining > 0:
            body = self.client.digests(series_id, min(MAX_DIGEST_PAGE, remaining), body=False, before=before)
            rows = body.get("digests", [])
            for row in rows:
                key = _digest_key(row)
                if self.known(key):
                    self.report["known"].append(key)
                    continue
                try:
                    src = _digest_source(self.client.digest(row["id"]), key)
                except (NyttigError, httpx.HTTPError, ValueError) as e:
                    self.report["failed"].append({"id": row["id"], "title": row.get("title", ""), "error": str(e)})
                    continue
                yield src
            remaining -= len(rows)
            if not rows or not body.get("has_more"):
                return
            before = rows[-1]["id"]

    def _item_source(self, item: dict[str, Any], key: str, include_unarchived: bool) -> Source | None:
        archive = item.get("archive") or {}
        status = archive.get("status", "")
        body, content = "", ""
        if status in ("done", "partial"):
            body = self._archived_text(item, archive["id"])
            content = "archive"
        if not body and include_unarchived:
            body = _description_text(item)
            content = "description"
        if not body:
            self.report["not_archived"].append({
                "id": item["id"], "title": item.get("title", ""), "link": item.get("link", ""),
                "archive_status": status or "never",
            })
            return None
        extra = {
            "nyttig_item_id": item["id"],
            "nyttig_source": item.get("source_name"),
            "nyttig_archive_id": archive.get("id"),
            "content": content,
            "scores": _item_scores(item),
        }
        return Source(
            title=item.get("title") or key,
            body=body,
            source=self.name,
            key=key,
            url=item.get("link") or None,
            author=item.get("author") or None,
            date=_time(item.get("published") or item.get("fetched_at")),
            tags=[t["name"] for t in item.get("tags", []) if t.get("name")],
            extra={k: v for k, v in extra.items() if v},
        )

    def _archived_text(self, item: dict[str, Any], archive_id: str) -> str:
        meta = self.client.archive(archive_id)
        data = self.client.archive_file(archive_id)
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        if "pdf" in meta.get("content_type", "") or data.startswith(b"%PDF"):
            body, _ = pdf_to_text(data)
        else:
            body, _ = html_to_markdown(data, url=meta.get("final_url") or item.get("link"))
        return body.strip()

    def _series_id(self, series: str) -> str:
        """A series id, or "<assessor>/<name>" with the names matched case-insensitively."""
        text = series.strip()
        if text.isdigit():
            return text
        assessor, sep, name = text.partition("/")
        if not sep or not assessor or not name:
            raise ValueError(f"series must be an id or ASSESSOR/NAME, not {series!r}")
        assessor_id = self._ref(assessor, self.client.assessors, "assessors", "assessor")
        rows = self.client.digest_series(assessor_id).get("series", [])
        for row in rows:
            if row.get("name", "").casefold() == name.casefold():
                return str(row["id"])
        valid = ", ".join(sorted(r.get("name", "") for r in rows)) or "none"
        raise ValueError(f"unknown series {name!r} for assessor {assessor!r}; valid: {valid}")

    @staticmethod
    def _ref(value: str | None, list_rows: Callable[[], dict[str, Any]], plural: str, kind: str) -> str | None:
        """A numeric id as is, or a name (any case) looked up in a list endpoint."""
        if value is None or value == "":
            return None
        text = str(value).strip()
        if text.isdigit():
            return text
        rows = list_rows().get(plural, [])
        for row in rows:
            if row.get("name", "").casefold() == text.casefold():
                return str(row["id"])
        valid = ", ".join(sorted(r.get("name", "") for r in rows)) or "none"
        raise ValueError(f"unknown {kind} {text!r}; valid: {valid}")


def _description_text(item: dict[str, Any]) -> str:
    text = item.get("description", "").strip()
    if not _HTML_TAG.search(text):
        return text
    body, _ = html_to_markdown(text, url=item.get("link") or None)
    return body.strip() or text


def _item_scores(item: dict[str, Any]) -> dict[str, float]:
    """Item-level scores by assessor name (assessments with no tag)."""
    return {
        a["assessor_name"]: a["score"]
        for a in item.get("assessments", [])
        if "score" in a and str(a.get("tag_id", "0")) == "0"
    }


def _digest_key(row: dict[str, Any]) -> str:
    return f"nyttig:digest:{row['id']}:{row.get('updated_at') or row.get('created_at', '')}"


def _digest_source(d: dict[str, Any], key: str) -> Source:
    return Source(
        title=d.get("title") or f"digest {d['id']}",
        body=d.get("body", ""),
        source="nyttig",
        key=key,
        date=_time(d.get("period_end")),
        tags=["digest"],
        extra={
            "nyttig_digest_id": d["id"],
            "series": f"{d.get('assessor_name', '')}/{d.get('series_name', '')}",
            "period_start": _time(d.get("period_start")),
            "period_end": _time(d.get("period_end")),
            "item_ids": [str(i["item_id"]) for i in d.get("items", [])],
            "input_digest_ids": [str(r["id"]) for r in d.get("inputs", [])],
        },
    )


def _time(value: str | None) -> str | None:
    return _FRACTION.sub(r"\1", value) if value else None


def _after(since: str | None) -> int | None:
    """since (a window like 7d, or an ISO time) as unix seconds; naive times are UTC."""
    value = parse_when(since)
    if not value:
        return None
    try:
        when = datetime.fromisoformat(_time(value).replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"bad since {since!r}") from e
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return int(when.timestamp())


def ingest_nyttig(bank: Bank, index: Index, client: NyttigClient, **params: Any) -> dict[str, Any]:
    index.sync(force=True)
    connector = NyttigConnector(client, known=lambda key: index.find_key(key) is not None)
    return _capture(bank, index, connector, connector.fetch(**params))


def ingest_nyttig_digests(
    bank: Bank, index: Index, client: NyttigClient, series: str, *, limit: int = 10,
) -> dict[str, Any]:
    index.sync(force=True)
    connector = NyttigConnector(client, known=lambda key: index.find_key(key) is not None)
    return _capture(bank, index, connector, connector.fetch_digests(series, limit=limit))


def _capture(bank: Bank, index: Index, connector: NyttigConnector, sources: Iterator[Source]) -> dict[str, Any]:
    result = add_sources(bank, index, sources, label="nyttig")
    not_archived = connector.report["not_archived"]
    return {
        "added": result.added,
        "skipped": result.skipped,
        "commit": result.commit,
        "known": len(connector.report["known"]),
        "not_archived": not_archived[:REPORT_CAP],
        "not_archived_count": len(not_archived),
        "failed": connector.report["failed"],
    }
