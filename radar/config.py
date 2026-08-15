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
        """Tutte le referenze, di tutti gli orologi monitorati."""
        out: list[str] = []
        for w in self.watches:
            out.extend(w.references)
        return out

    @property
    def watches(self) -> list["WatchView"]:
        """Gli orologi monitorati.

        Accetta sia la forma `watches:` (lista) sia la vecchia `watch:`
        (singolo), cosi' le configurazioni esistenti continuano a funzionare.
        """
        raw = self._d.get("watches")
        if raw is None:
            single = self._d.get("watch")
            raw = [single] if single else []
        return [WatchView(self, w) for w in raw]


class WatchView:
    """La configurazione vista dal punto di vista di UN orologio.

    Ogni orologio puo' ridefinire qualsiasi chiave globale: prezzo di partenza,
    moltiplicatori, preferenze, soglie di notifica. Quello che non ridefinisce
    lo eredita. Cosi' un Daytona ha la sua curva di deprezzamento senza dover
    duplicare tutto il resto del file.
    """

    def __init__(self, cfg: "Config", watch: dict):
        self.cfg = cfg
        self.watch = watch or {}

    @property
    def id(self) -> str:
        raw = (self.watch.get("id") or self.watch.get("nickname")
               or self.watch.get("model") or "watch")
        return str(raw).strip().lower().replace(" ", "-")

    @property
    def label(self) -> str:
        base = f"{self.watch.get('brand', '')} {self.watch.get('model', '')}".strip()
        nick = self.watch.get("nickname")
        if nick:
            return f"{base} \u201c{nick}\u201d" if base else str(nick)
        return base or self.id

    @property
    def references(self) -> list[str]:
        return [str(r).upper().replace(" ", "") for r in self.watch.get("references", [])]

    @property
    def model_keywords(self) -> list[str]:
        return self.watch.get("model_keywords", [])

    @property
    def exclude_keywords(self) -> list[str]:
        return self.watch.get("exclude_keywords", [])

    def get(self, dotted: str, default: Any = None) -> Any:
        """Valore dell'orologio, fuso con quello globale.

        La fusione è ricorsiva e questo è il punto: se un orologio ridefinisce
        `preferences.target_years`, deve ereditare comunque tutto il resto di
        `preferences`. Senza fusione ridefinire una chiave ne cancellava
        un'intera sezione — e il difetto era invisibile, perché il sistema
        continuava a funzionare usando i valori di ripiego del codice.
        """
        own = _dig(self.watch, dotted)
        glob = self.cfg.get(dotted, None)
        if own is None:
            return glob if glob is not None else default
        if isinstance(own, dict) and isinstance(glob, dict):
            return _fondi(glob, own)
        return own

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WatchView {self.id} {self.references}>"


def _fondi(base: dict, sopra: dict) -> dict:
    """Fusione ricorsiva: `sopra` vince chiave per chiave, non in blocco."""
    out = dict(base)
    for k, v in sopra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _fondi(out[k], v)
        else:
            out[k] = v
    return out


def _dig(data: Any, dotted: str) -> Any:
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


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
