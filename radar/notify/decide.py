"""Decide COSA notificare. È la parte che ti evita 120 notifiche per un annuncio.

Regole:
  • annuncio nuovo e interessante          → notifica
  • annuncio già visto, prezzo sceso       → notifica (solo il calo)
  • annuncio già visto, score migliorato   → notifica
  • annuncio già visto, tutto uguale       → silenzio
  • annuncio sotto mercato                 → notifica sempre, anche score basso
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import Config
from ..models import Listing

ROME = ZoneInfo("Europe/Rome")


def tier_geografico(listing: Listing, cfg, ignoto_come: str = "nearby") -> str:
    """In quale dei tre piani sta questo annuncio.

    `home`      Italia e San Marino: dove compreresti davvero, andando a vedere
    `nearby`    Europa: raggiungibile, ma deve valerne la pena
    `reference` il resto del mondo: serve a capire i prezzi, non a comprare

    La distinzione non e' una preferenza da pesare nel punteggio: e' un
    cancello. Un Royal Oak a Singapore a un prezzo ottimo resta un dato di
    mercato utile, ma non e' una cosa che ti riguarda — e svegliarti per
    quello significa insegnarti a ignorare le notifiche.
    """
    geo = cfg.get("preferences.geography", {}) or {}
    c = listing.seller_country
    if c is None:
        return ignoto_come
    if c in (geo.get("home") or []):
        return "home"
    if c in (geo.get("nearby") or []):
        return "nearby"
    return "reference"


@dataclass
class NotifyDecision:
    listing: Listing
    reason: str          # new | price_drop | score_up | underpriced
    headline: str
    priority: int        # più alto = più urgente
    tier: str = "home"   # home | nearby


def decide_notifications(
    scored: list[tuple[Listing, dict]],
    cfg: Config,
    is_underpriced,
    force: bool = False,
) -> list[NotifyDecision]:
    n = cfg.get("notifications", {})
    min_score = int(n.get("min_score", 78))
    nearby_min_score = int(n.get("nearby_min_score", 90))
    nearby_under = float(n.get("nearby_underpriced_pct", 15.0))
    notify_reference = bool(n.get("notify_reference", False))
    ignoto_come = str(n.get("unknown_location_as", "nearby"))
    drop_pct = float(n.get("price_drop_pct", 2.0))
    drop_abs = float(n.get("price_drop_abs_eur", 400))
    score_up = int(n.get("score_improvement", 6))
    always_under = float(n.get("always_notify_if_underpriced_pct", 6.0))
    max_per_run = int(n.get("max_per_run", 12))

    out: list[NotifyDecision] = []

    for listing, change in scored:
        tier = tier_geografico(listing, cfg, ignoto_come)
        if tier == "reference" and not notify_reference:
            # resta nel database e nell'indice, ma non ti disturba
            continue
        soglia = min_score if tier == "home" else nearby_min_score
        soglia_sotto = always_under if tier == "home" else nearby_under

        under = is_underpriced(listing, soglia_sotto)
        is_new = change.get("is_new")
        old_price = change.get("old_price")
        old_score = change.get("old_score") or 0

        decision = None

        if is_new:
            if under:
                decision = NotifyDecision(
                    listing, "underpriced",
                    f"SOTTO MERCATO · {listing.delta_eur:+,.0f} €", 100,
                )
            elif listing.score >= soglia:
                decision = NotifyDecision(listing, "new", "NUOVO ANNUNCIO", 70)
        else:
            if old_price and listing.price_eur:
                delta = old_price - listing.price_eur
                pct = delta / old_price * 100 if old_price else 0
                if delta > 0 and (pct >= drop_pct or delta >= drop_abs):
                    decision = NotifyDecision(
                        listing, "price_drop",
                        f"PREZZO SCESO · −{delta:,.0f} € ({pct:.1f}%)", 90,
                    )
            if decision is None and listing.score - old_score >= score_up \
                    and listing.score >= soglia:
                decision = NotifyDecision(
                    listing, "score_up",
                    f"MIGLIORATO · {old_score} → {listing.score}", 60,
                )
            if decision is None and under and old_score < soglia <= listing.score:
                decision = NotifyDecision(listing, "underpriced", "SOTTO MERCATO", 95)

        if decision:
            decision.tier = tier
            if tier != "home":
                decision.priority -= 5      # a parità, l'Italia passa prima
            out.append(decision)

    out.sort(key=lambda d: (-d.priority, -d.listing.score))

    if not force and _in_quiet_hours(n.get("quiet_hours", [23, 7])):
        # nelle ore di silenzio passa solo l'affare vero
        out = [d for d in out if d.priority >= 90]

    return out[:max_per_run]


def _in_quiet_hours(window) -> bool:
    try:
        start, end = int(window[0]), int(window[1])
    except (TypeError, ValueError, IndexError):
        return False
    h = datetime.now(ROME).hour
    return h >= start or h < end if start > end else start <= h < end
