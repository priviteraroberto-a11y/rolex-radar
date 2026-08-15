"""Punteggio 0-100 di un annuncio.

Regola di progetto: nessun criterio (tranne la referenza) può azzerare un
annuncio. Tutto pesa, niente esclude. È così che un 2023 eccezionale a prezzo
basso riesce a superare un 2025 mediocre a prezzo pieno.
"""
from __future__ import annotations

from .config import Config
from .models import Listing


class Scorer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.w = cfg.get("scoring.weights", {})
        self.full_credit = float(cfg.get("scoring.price_full_credit_discount_pct", 10.0))
        self.zero_credit = float(cfg.get("scoring.price_zero_credit_premium_pct", 12.0))
        self.prefs = cfg.get("preferences", {})

    def score(self, l: Listing) -> Listing:
        b: dict[str, float] = {}
        reasons: list[str] = []

        b["price_vs_fair"] = self._price_points(l, reasons)
        b["year"] = self._year_points(l, reasons)
        b["condition"] = self._condition_points(l, reasons)
        b["full_set"] = self._full_set_points(l, reasons)
        b["bracelet"] = self._bracelet_points(l, reasons)
        b["warranty"] = self._warranty_points(l, reasons)
        b["never_polished"] = self._polish_points(l, reasons)
        b["seller_trust"] = self._seller_points(l, reasons)
        b["seller_location"] = self._location_points(l, reasons)

        total = sum(b.values())

        # Penalità dati mancanti: un annuncio opaco non merita di stare in cima.
        unknowns = sum(
            1 for v in (l.year, l.condition, l.full_set, l.bracelet,
                        l.warranty_region, l.seller_country)
            if v is None
        )
        if unknowns >= 4:
            total -= 6
            reasons.append(f"⚠︎ {unknowns} caratteristiche non dichiarate")
        if l.price_eur is None:
            total -= 15
            reasons.append("⚠︎ prezzo su richiesta")

        l.score = max(0, min(100, round(total)))
        l.score_breakdown = {k: round(v, 1) for k, v in b.items()}
        l.reasons = reasons
        return l

    # -- singole voci ---------------------------------------------------------

    def _price_points(self, l: Listing, reasons: list[str]) -> float:
        w = float(self.w.get("price_vs_fair", 40))
        if l.delta_pct is None:
            return w * 0.35
        d = l.delta_pct                       # >0 = sotto il fair value
        if d >= self.full_credit:
            reasons.append(f"💰 {d:.1f}% sotto il valore stimato ({l.delta_eur:+,.0f} €)")
            return w
        if d <= -self.zero_credit:
            reasons.append(f"prezzo {abs(d):.1f}% sopra il valore stimato")
            return 0.0
        # interpolazione lineare fra -zero_credit e +full_credit
        span = self.full_credit + self.zero_credit
        pts = w * (d + self.zero_credit) / span
        if d > 2:
            reasons.append(f"prezzo {d:.1f}% sotto il valore stimato")
        return pts

    def _year_points(self, l: Listing, reasons: list[str]) -> float:
        w = float(self.w.get("year", 12))
        if l.year is None:
            return w * 0.4
        p = self.prefs
        if l.year in p.get("target_years", []):
            reasons.append(f"anno {l.year} — quello che cerchi")
            return w
        if l.year in p.get("acceptable_years", []):
            reasons.append(f"anno {l.year}")
            return w * 0.8
        if l.year in p.get("tolerated_years", []):
            reasons.append(f"anno {l.year} — più vecchio del target")
            return w * 0.5
        return w * 0.2

    def _condition_points(self, l: Listing, reasons: list[str]) -> float:
        w = float(self.w.get("condition", 12))
        ranks = self.prefs.get("condition_rank", [])
        if l.condition is None or l.condition not in ranks:
            return w * 0.45
        idx = ranks.index(l.condition)
        pts = w * (1 - idx / max(1, len(ranks) - 1))
        if idx <= 1:
            reasons.append(f"condizioni: {l.condition}")
        return pts

    def _full_set_points(self, l: Listing, reasons: list[str]) -> float:
        w = float(self.w.get("full_set", 10))
        if l.full_set is True:
            reasons.append("full set")
            return w
        if l.full_set is False:
            return 0.0
        return w * 0.35

    def _bracelet_points(self, l: Listing, reasons: list[str]) -> float:
        w = float(self.w.get("bracelet", 8))
        pref = self.prefs.get("bracelet", {}).get("preferred", "jubilee")
        if l.bracelet == pref:
            reasons.append(f"bracciale {pref}")
            return w
        if l.bracelet is None:
            return w * 0.45
        reasons.append(f"bracciale {l.bracelet} (non {pref})")
        return w * 0.2

    def _warranty_points(self, l: Listing, reasons: list[str]) -> float:
        w = float(self.w.get("warranty", 8))
        regions = self.prefs.get("warranty_regions", {})
        r = l.warranty_region
        if r is None:
            return w * 0.4
        if r in regions.get("preferred", []):
            reasons.append(f"garanzia {r}")
            return w
        if r in regions.get("neutral", []):
            return w * 0.6
        if r in regions.get("disliked", []):
            reasons.append(f"⚠︎ garanzia {r} — rivendibilità più difficile in Italia")
            return w * 0.1
        return w * 0.4

    def _polish_points(self, l: Listing, reasons: list[str]) -> float:
        w = float(self.w.get("never_polished", 5))
        if l.never_polished is True:
            reasons.append("mai lucidato")
            return w
        if l.never_polished is False:
            return 0.0
        return w * 0.5

    def _location_points(self, l: Listing, reasons: list[str]) -> float:
        """Dove si trova l'orologio.

        Non è una questione di valore intrinseco — un Overseas non vale di più
        perché sta a Milano — ma di quanto è comprabile: puoi vederlo, non paghi
        dogana, e se qualcosa va storto hai un interlocutore raggiungibile.
        Per questo pesa nel punteggio e NON nella stima di valore.
        """
        w = float(self.w.get("seller_location", 0))
        if not w:
            return 0.0
        geo = self.prefs.get("seller_locations", {})
        c = l.seller_country
        if c is None:
            return w * 0.4
        if c in geo.get("preferred", []):
            reasons.append(f"📍 si trova in {c}")
            return w
        if c in geo.get("neutral", []):
            return w * 0.55
        if c in geo.get("distant", []):
            reasons.append(f"⚠︎ si trova in {c} — dogana, spedizione, resi complicati")
            return w * 0.05
        return w * 0.3

    def _seller_points(self, l: Listing, reasons: list[str]) -> float:
        w = float(self.w.get("seller_trust", 5))
        trust = max(0, min(5, int(l.seller_trust or 0)))
        if trust >= 4:
            reasons.append(f"venditore affidabile ({l.source})")
        return w * trust / 5


def stars(score: int) -> str:
    """Rappresentazione a stelle per la notifica."""
    # int(x + 0.5) invece di round(): round() in Python arrotonda 2.5 → 2
    filled = max(1, min(5, int(score / 20 + 0.5)))
    return "★" * filled + "☆" * (5 - filled)
