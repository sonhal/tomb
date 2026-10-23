"""Settings from the environment (TOME_*) and explicit overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """A missing or invalid setting."""


@dataclass(frozen=True)
class Settings:
    home: Path
    host: str = "127.0.0.1"
    port: int = 8765
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    nyttig_url: str | None = None
    nyttig_user: str | None = None
    nyttig_password: str | None = None

    @classmethod
    def from_env(cls, **overrides: Any) -> Settings:
        """Read TOME_* variables; overrides that are None are ignored."""
        values: dict[str, Any] = {
            "home": os.environ.get("TOME_HOME"),
            "host": os.environ.get("TOME_HOST"),
            "port": os.environ.get("TOME_PORT"),
            "allowed_hosts": _csv(os.environ.get("TOME_ALLOWED_HOSTS")),
            "allowed_origins": _csv(os.environ.get("TOME_ALLOWED_ORIGINS")),
            "nyttig_url": os.environ.get("TOME_NYTTIG_URL"),
            "nyttig_user": os.environ.get("TOME_NYTTIG_USER"),
            "nyttig_password": os.environ.get("TOME_NYTTIG_PASSWORD"),
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        values = {k: v for k, v in values.items() if v is not None}
        if not values.get("home"):
            raise ConfigError("no bank given: pass --home or set TOME_HOME")
        if "port" in values:
            values["port"] = _port(values["port"])
        values["home"] = Path(values["home"]).expanduser()
        return cls(**values)


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


def _port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"port must be a number, not {value!r}") from e
    if not 0 < port < 65536:
        raise ConfigError(f"port out of range: {port}")
    return port
