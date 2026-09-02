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

    def riguarda_un_orologio(self, testo: str) -> bool:
        """Filtro grossolano: questo testo nomina uno degli orologi seguiti?

        Lo usa il lettore delle email per decidere se un blocco vale la pena
        di essere estratto. Deve essere generoso: e' il primo setaccio, e chi
        passa viene comunque ripesato dal filtro vero.
        """
        from .extract import matches_reference
        if matches_reference(None, testo, self.references):
            return True
        return any(w.matches(testo, testo) for w in self.watches
                   if w.identify_by == "name")

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
        """Referenze complete piu' le radici che bastano a riconoscerle.

        Un venditore su tre scrive la referenza per intero. Gli altri scrivono
        "Royal Oak 37mm 15450ST", "Black Bay Chrono 79360N", "El Primero
        A384": la radice c'e', il suffisso del quadrante no. Pretendere la
        stringa completa significava scartarli tutti — sette orologi su otto
        erano di fatto invisibili.

        Il rischio e' accettare un quadrante diverso da quello che volevi. E'
        il rischio giusto da correre: e' l'orologio che cerchi, il punteggio
        e le parole escluse fanno il resto, e vederne uno in piu' costa molto
        meno che perderne uno.
        """
        grezze = list(self.watch.get("references", []))
        grezze += list(self.watch.get("reference_stems", []))
        return [str(r).upper().replace(" ", "") for r in grezze if str(r).strip()]

    @property
    def search_terms(self) -> list[str]:
        """Cosa scrivere nella casella di ricerca dei negozi.

        Il ripiego non e' un dettaglio: un orologio riconosciuto per nome ha
        `references: []`, e senza ripiego la lista resta vuota. Nessun termine
        significa nessun indirizzo da interrogare, quindi nessuna fonte
        contattata e zero annunci — senza un errore, senza una riga nei log.
        E' successo davvero, allo Speedmaster Snoopy: il radar non e' che non
        lo trovasse, non lo stava proprio cercando.
        """
        propri = [str(t) for t in (self.watch.get("search_terms") or []) if str(t).strip()]
        if propri:
            return propri
        if self.references:
            return self.references
        parole = " ".join(self.must_include)
        ripiego = [f"{self.watch.get('model', '')} {parole}".strip(),
                   parole.strip(),
                   f"{self.brand} {self.watch.get('model', '')}".strip()]
        return [t for t in dict.fromkeys(ripiego) if t]

    @property
    def references_esatte(self) -> list[str]:
        """Solo le referenze complete, senza le radici.

        Distinzione necessaria: la radice `310.30.42.50.01` combacia sia con
        l'Hesalite sia con lo zaffiro, quindi non puo' essere usata per
        decidere quale delle due varianti sta davanti.
        """
        return [str(r).upper().replace(" ", "")
                for r in self.watch.get("references", []) if str(r).strip()]

    @property
    def exclude_references(self) -> list[str]:
        """Varianti da scartare quando il venditore le dichiara."""
        return [str(r).upper().replace(" ", "")
                for r in self.watch.get("exclude_references", [])]

    @property
    def model_keywords(self) -> list[str]:
        return self.watch.get("model_keywords", [])

    @property
    def brand(self) -> str:
        return str(self.watch.get("brand", "") or "")

    @property
    def identify_by(self) -> str:
        """`reference` (predefinito) oppure `name`.

        Il modo giusto dipende dall'orologio, non dal sistema: per un Rolex
        la referenza e' nel titolo di ogni annuncio, per uno Zenith Elite
        quasi mai.
        """
        return str(self.watch.get("identify_by", "reference")).lower()

    @property
    def must_include(self) -> list[str]:
        """Parole che devono comparire tutte, quando si riconosce dal nome."""
        return list(self.watch.get("must_include") or self.model_keywords)

    def matches(self, title: str, text: str) -> bool:
        """Questo annuncio parla di questo orologio?"""
        from . import extract
        if self.identify_by == "name":
            return extract.matches_by_name(title, text, self.brand,
                                           self.must_include, self.exclude_keywords)
        return extract.is_target_watch(title, text, self.references,
                                       self.model_keywords, self.exclude_keywords)

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
