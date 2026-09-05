"""Strutture dati del sistema."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Listing:
    """Un annuncio normalizzato, indipendente dalla fonte."""

    source: str
    url: str
    title: str = ""
    raw_text: str = ""

    raw_price: Optional[str] = None             # stringa prezzo così come letta
    price_eur: Optional[float] = None
    currency: str = "EUR"
    price_original: Optional[float] = None      # prezzo nella valuta originale

    reference: Optional[str] = None
    year: Optional[int] = None
    serial: Optional[str] = None

    bracelet: Optional[str] = None              # jubilee | oyster | None
    condition: Optional[str] = None             # unworn | new | mint | ...
    full_set: Optional[bool] = None
    never_polished: Optional[bool] = None

    warranty_region: Optional[str] = None       # IT | EU | AE | ...
    warranty_year: Optional[int] = None

    seller: Optional[str] = None
    seller_country: Optional[str] = None
    is_dealer: Optional[bool] = None
    seller_trust: int = 0                       # 0..5, da config o dalla fonte

    image: Optional[str] = None
    sold: Optional[bool] = None          # gia venduto / non disponibile

    # calcolati a valle
    fair_value_eur: Optional[float] = None
    delta_eur: Optional[float] = None
    delta_pct: Optional[float] = None
    # Scarto rispetto al prezzo di LISTINO, non al mercato: dato statico,
    # informativo, che non entra nel punteggio.
    listino_eur: Optional[float] = None
    delta_listino_pct: Optional[float] = None
    score: int = 0
    score_breakdown: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)

    @property
    def key(self) -> str:
        """Identità stabile dell'annuncio. L'URL è la chiave primaria."""
        normalized = self.url.split("?")[0].rstrip("/").lower()
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)

    def __repr__(self) -> str:  # pragma: no cover
        p = f"{self.price_eur:,.0f}€" if self.price_eur else "n/d"
        return f"<Listing {self.source} {self.year or '????'} {p} score={self.score}>"
