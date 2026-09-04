"""Motore di fair value.

L'idea
------
Confrontare direttamente due annunci è inutile: un 2023 lucidato senza scatola
e un 2026 unworn full set non sono lo stesso oggetto. Quindi:

1. Ogni annuncio viene NORMALIZZATO dividendo il prezzo per il prodotto dei
   moltiplicatori edonici delle sue caratteristiche. Il risultato è il prezzo
   che quell'annuncio avrebbe se fosse "l'esemplare di riferimento"
   (2025, unworn, jubilee, full set, garanzia EU).
2. La MEDIANA di questi prezzi normalizzati è l'indice di mercato.
   La mediana (non la media) perché è immune agli annunci-civetta e ai POA.
3. Il fair value di un annuncio specifico = indice × i suoi moltiplicatori.

Questo è, in piccolo, quello che fa un modello edonico. È il motivo per cui il
sistema riesce a dirti "questo 2023 a 26.000 € è un affare migliore di quel
2025 a 33.000 €", cosa che nessun marketplace ti dice.
"""
from __future__ import annotations

import statistics
from typing import Optional

from .config import Config, multiplier
from .models import Listing


class FairValueEngine:
    def __init__(self, cfg: Config, comparables: list[dict]):
        self.cfg = cfg
        self.mult = cfg.get("fair_value.multipliers", {})
        self.seed = float(cfg.get("fair_value.seed_price_eur", 30000))
        self.min_samples = int(cfg.get("fair_value.min_samples", 10))

        self.samples = self._normalized_samples(comparables)
        self.n = len(self.samples)
        self.data_driven = self.n >= self.min_samples

        if self.data_driven:
            self.index = statistics.median(self.samples)
        else:
            self.index = self.seed

        self.median_raw = (
            statistics.median([c["price_eur"] for c in comparables if c.get("price_eur")])
            if comparables else None
        )
        prices = sorted(c["price_eur"] for c in comparables if c.get("price_eur"))
        self.p25 = prices[len(prices) // 4] if len(prices) >= 4 else None
        self.cheapest = prices[0] if prices else None

    # ------------------------------------------------------------------

    def _factor(self, year, condition, bracelet, full_set, warranty, never_polished) -> float:
        m = self.mult
        f = 1.0
        f *= multiplier(m.get("year", {}), year)
        f *= multiplier(m.get("condition", {}), condition)
        f *= multiplier(m.get("bracelet", {}), bracelet)
        f *= multiplier(m.get("full_set", {}), _tri(full_set), "_default") if m.get("full_set") else 1.0
        f *= multiplier(m.get("warranty_region", {}), warranty)
        f *= multiplier(m.get("never_polished", {}), _tri(never_polished), "_default") if m.get("never_polished") else 1.0
        return f

    def factor_for(self, l: Listing) -> float:
        return self._factor(l.year, l.condition, l.bracelet, l.full_set,
                            l.warranty_region, l.never_polished)

    def _normalized_samples(self, comparables: list[dict]) -> list[float]:
        out: list[float] = []
        for c in comparables:
            price = c.get("price_eur")
            if not price:
                continue
            f = self._factor(
                c.get("year"), c.get("condition"), c.get("bracelet"),
                _from_int(c.get("full_set")), c.get("warranty_region"),
                _from_int(c.get("never_polished")),
            )
            if f > 0:
                out.append(price / f)
        if len(out) < 4:
            return out
        # taglio dei code: via il 10% più basso e più alto, sono quasi sempre errori
        out.sort()
        k = max(1, len(out) // 10)
        return out[k:-k] if len(out) > 2 * k + 3 else out

    # ------------------------------------------------------------------

    def evaluate(self, l: Listing) -> Listing:
        """Assegna fair_value_eur, delta_eur, delta_pct all'annuncio."""
        fv = self.index * self.factor_for(l)
        l.fair_value_eur = round(fv, 0)
        if l.price_eur:
            l.delta_eur = round(fv - l.price_eur, 0)
            l.delta_pct = round((fv - l.price_eur) / fv * 100, 2) if fv else None
        return l

    def is_underpriced(self, l: Listing, threshold_pct: Optional[float] = None) -> bool:
        thr = threshold_pct if threshold_pct is not None else float(
            self.cfg.get("fair_value.underpriced_threshold_pct", 4.0)
        )
        return l.delta_pct is not None and l.delta_pct >= thr

    def summary(self) -> dict:
        return {
            "index": round(self.index, 0),
            "samples": self.n,
            "data_driven": self.data_driven,
            "median_raw": round(self.median_raw, 0) if self.median_raw else None,
            "p25": round(self.p25, 0) if self.p25 else None,
            "cheapest": round(self.cheapest, 0) if self.cheapest else None,
        }


def _tri(v):
    """None → None (ignoto), True → 'true', False → 'false' (chiavi YAML).

    Prima l'ignoto diventava la stringa "_default", che nella tabella del
    corredo non esiste: il moltiplicatore usciva 1.0, cioe' come se avesse
    scatola e documenti. Ora l'ignoto arriva a `multiplier` come tale e prende
    la media fra "si'" e "no", che e' quello che si sa davvero.
    """
    if v is None:
        return None
    return "true" if v else "false"


def _from_int(v):
    return None if v is None else bool(v)
