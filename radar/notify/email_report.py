"""Report email HTML (SMTP)."""
from __future__ import annotations

import html
import logging
import os
import smtplib
from email.message import EmailMessage

from .decide import NotifyDecision

log = logging.getLogger("radar.email_out")


class EmailNotifier:
    def __init__(self):
        self.host = os.environ.get("SMTP_HOST", "")
        self.port = int(os.environ.get("SMTP_PORT", "587"))
        self.user = os.environ.get("SMTP_USER", "")
        self.password = os.environ.get("SMTP_PASS", "")
        self.to = os.environ.get("REPORT_TO", self.user)
        self.enabled = bool(self.host and self.user and self.password and self.to)

    def send_digest(self, decisions: list[NotifyDecision], market: dict) -> bool:
        if not self.enabled or not decisions:
            return False
        msg = EmailMessage()
        top = decisions[0].listing
        msg["Subject"] = (
            f"Rolex Radar · {len(decisions)} segnalazion"
            f"{'e' if len(decisions) == 1 else 'i'} · migliore {top.score}/100"
        )
        msg["From"] = self.user
        msg["To"] = self.to
        msg.set_content(_plain(decisions, market))
        msg.add_alternative(_html(decisions, market), subtype="html")

        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as s:
                s.starttls()
                s.login(self.user, self.password)
                s.send_message(msg)
            return True
        except Exception as exc:
            log.error("Invio email fallito: %s", exc)
            return False


def _eur(v) -> str:
    return f"{v:,.0f} €".replace(",", ".") if v else "—"


def _plain(decisions, market) -> str:
    lines = ["ROLEX RADAR", ""]
    lines.append(f"Indice di mercato stimato: {_eur(market.get('index'))} "
                 f"({market.get('samples', 0)} annunci)")
    lines.append("")
    for d in decisions:
        l = d.listing
        lines += [
            f"[{l.score}/100] {d.headline}",
            f"  {l.year or '????'} · {l.condition or 'n/d'} · {l.bracelet or 'n/d'} · "
            f"garanzia {l.warranty_region or 'n/d'}",
            f"  Prezzo {_eur(l.price_eur)} · stima {_eur(l.fair_value_eur)}",
            f"  {l.url}",
            "",
        ]
    return "\n".join(lines)


def _html(decisions, market) -> str:
    cards = []
    for d in decisions:
        l = d.listing
        e = html.escape
        color = "#16a34a" if (l.delta_pct or 0) > 0 else "#64748b"
        chips = "".join(
            f'<span style="display:inline-block;background:#f1f5f9;border-radius:99px;'
            f'padding:3px 10px;margin:2px 4px 2px 0;font-size:12px;color:#334155">{e(str(c))}</span>'
            for c in [
                l.year, (l.condition or "").replace("_", " ") or None, l.bracelet,
                f"garanzia {l.warranty_region}" if l.warranty_region else None,
                "full set" if l.full_set else None,
                "mai lucidato" if l.never_polished else None,
            ] if c
        )
        img = (f'<img src="{e(l.image)}" width="110" style="border-radius:8px;'
               f'object-fit:cover" alt="">' if l.image else "")
        cards.append(f"""
        <tr><td style="padding:14px 0;border-bottom:1px solid #e2e8f0">
          <table width="100%" cellpadding="0" cellspacing="0"><tr>
            <td valign="top" width="120">{img}</td>
            <td valign="top">
              <div style="font-size:12px;letter-spacing:.08em;color:{color};
                          font-weight:700">{e(d.headline)}</div>
              <div style="font-size:17px;font-weight:600;margin:4px 0 2px">
                {_eur(l.price_eur)}
                <span style="font-size:13px;font-weight:400;color:#64748b">
                  · stima {_eur(l.fair_value_eur)}</span>
              </div>
              <div style="margin:6px 0">{chips}</div>
              <div style="font-size:13px;color:#334155">
                Score <b>{l.score}/100</b> · fonte {e(l.source)}</div>
              <a href="{e(l.url)}" style="display:inline-block;margin-top:8px;
                 background:#0f172a;color:#fff;text-decoration:none;padding:7px 14px;
                 border-radius:6px;font-size:13px">Apri l'annuncio</a>
            </td>
          </tr></table>
        </td></tr>""")

    return f"""<!doctype html><html><body style="margin:0;background:#f8fafc;
      font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
      <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
        <table width="600" cellpadding="0" cellspacing="0"
               style="background:#fff;margin:24px;border-radius:12px;padding:24px">
          <tr><td>
            <div style="font-size:12px;letter-spacing:.16em;color:#94a3b8">ROLEX RADAR</div>
            <h1 style="margin:6px 0 2px;font-size:22px;color:#0f172a">
              GMT-Master II “Pepsi” · 126710BLRO</h1>
            <div style="font-size:13px;color:#64748b;margin-bottom:6px">
              Indice di mercato stimato <b>{_eur(market.get('index'))}</b>
              · {market.get('samples', 0)} annunci nel campione
              {'' if market.get('data_driven') else ' · stima di partenza (dati insufficienti)'}
            </div>
          </td></tr>
          {''.join(cards)}
          <tr><td style="padding-top:16px;font-size:11px;color:#94a3b8">
            Report automatico. Le stime sono indicative e non costituiscono
            consulenza all'acquisto.
          </td></tr>
        </table>
      </td></tr></table></body></html>"""
