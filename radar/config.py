"""Caricamento e accesso alla configurazione."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class Config:
    def __init__(self, data: dict):
        self._d = data

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        p = Path(path or os.environ.get("RADAR_CONFIG", DEFAULT_PATH))
        with open(p, "r", encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh))

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self._d
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def __getitem__(self, k: str) -> Any:
        return self._d[k]

    @property
    def raw(self) -> dict:
        return self._d

    @property
    def sources(self) -> list[dict]:
        return [s for s in self._d.get("sources", []) if s.get("enabled")]

    @property
    def all_sources(self) -> list[dict]:
        return self._d.get("sources", [])

    @property
    def references(self) -> list[str]:
        return [r.upper().replace(" ", "") for r in self.get("watch.references", [])]


def multiplier(table: dict, key: Any, default_key: str = "_default") -> float:
    """Lookup tollerante nelle tabelle di moltiplicatori (chiavi YAML miste)."""
    if table is None:
        return 1.0
    if key is None:
        return float(table.get(default_key, 1.0))
    for candidate in (key, str(key), str(key).lower()):
        if candidate in table:
            return float(table[candidate])
    return float(table.get(default_key, 1.0))
