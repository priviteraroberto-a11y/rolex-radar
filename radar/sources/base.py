"""Contratto comune a tutte le fonti."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import Listing


@dataclass
class SourceResult:
    name: str
    ok: bool
    listings: list[Listing]
    detail: str = ""


class BaseSource:
    def __init__(self, cfg: dict, ctx: Any):
        self.cfg = cfg
        self.ctx = ctx           # ha .fetcher e .config
        self.name = cfg.get("name", "unnamed")

    def collect(self) -> SourceResult:  # pragma: no cover - interfaccia
        raise NotImplementedError
