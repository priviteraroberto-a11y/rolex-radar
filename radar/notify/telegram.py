"""Notifiche Telegram."""
from __future__ import annotations

import html
import logging
import os

import requests

from ..models import Listing
from ..scorer import stars
from .decide import NotifyDecision

log = logging.getLogger("radar.telegram")

REASON_ICON = {
    "underpriced": "🔥",
    "price_drop": "📉",
    "new": "🚨",
    "score_up": "⬆️",
}


class TelegramNotifier:
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id)

    def send(self, decision: NotifyDecision) -> bool:
        if not self.enabled:
            log.warning("Telegram non configurato: notifica saltata")
            return False
        l = decision.listing
        text = self._format(decision)
        try:
            if l.image:
                r = requests.post(
                    f"https://api.telegram.org/bot{self.token}/sendPhoto",
                    data={"chat_id": self.chat_id, "photo": l.image,
                          "caption": text[:1024], "parse_mode": "HTML"},
                    timeout=20,
                )
                if r.ok:
                    return True
            r = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": "false"},
                timeout=20,
            )
            if not r.ok:
                log.error("Telegram error %s: %s", r.status_code, r.text[:300])
            return r.ok
        except requests.RequestException as exc:
            log.error("Telegram fallito: %s", exc)
            return False

    def send_text(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=20,
            )
            return r.ok
        except requests.RequestException:
            return False

    # ------------------------------------------------------------------

    def _format(self, d: NotifyDecision) -> str:
        l = d.listing
        e = html.escape
        icon = REASON_ICON.get(d.reason, "🔔")

        lines = [
            f"{icon} <b>{e(d.headline)}</b>",
            "",
            "<b>Rolex GMT-Master II “Pepsi”</b>",
            f"<code>{e(l.reference or '126710BLRO')}</code>",
            "",
        ]

        def row(label: str, value) -> None:
            if value not in (None, "", False):
                lines.append(f"{label}  <b>{e(str(value))}</b>")

        row("Prezzo    ", f"{l.price_eur:,.0f} €".replace(",", ".") if l.price_eur else "su richiesta")
        if l.fair_value_eur:
            fv = f"{l.fair_value_eur:,.0f} €".replace(",", ".")
            row("Stima     ", fv)
        if l.delta_eur is not None:
            sign = "sotto" if l.delta_eur > 0 else "sopra"
            row("Scarto    ", f"{abs(l.delta_eur):,.0f} € {sign} ({l.delta_pct:+.1f}%)".replace(",", "."))
        if l.delta_listino_pct is not None:
            verso = "sotto" if l.delta_listino_pct >= 0 else "sopra"
            listino = f"{l.listino_eur:,.0f} €".replace(",", ".")
            row("Listino   ", f"{listino} — {abs(l.delta_listino_pct):.0f}% {verso}")
        lines.append("")
        row("Anno      ", l.year)
        row("Condizioni", _pretty(l.condition))
        row("Bracciale ", _pretty(l.bracelet))
        row("Si trova  ", l.seller_country)
        row("Garanzia  ", l.warranty_region)
        row("Full set  ", "sì" if l.full_set else ("no" if l.full_set is False else None))
        row("Lucidatura", "mai lucidato" if l.never_polished else None)
        row("Fonte     ", l.source)

        lines += ["", f"<b>Score {l.score}/100</b>  {stars(l.score)}"]

        if l.reasons:
            lines.append("")
            for r in l.reasons[:6]:
                lines.append(f"· {e(r)}")

        lines += ["", f'<a href="{e(l.url)}">→ Apri l\'annuncio</a>']
        return "\n".join(lines)


def _pretty(v):
    return v.replace("_", " ") if isinstance(v, str) else v
