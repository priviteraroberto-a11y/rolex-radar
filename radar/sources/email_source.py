"""Ingestione degli alert email dei marketplace.

Perché questo modulo esiste
---------------------------
Chrono24, Watchfinder, WatchCharts e simili sono protetti da DataDome /
Cloudflare: lo scraping diretto o non funziona, o funziona per due settimane e
poi si rompe, oltre a violare i loro termini d'uso.

Però quegli stessi siti offrono le "ricerche salvate" con notifica email.
Quindi la strada pulita è: tu salvi la ricerca sul marketplace, loro ti mandano
l'email, e il sistema legge la tua casella e ne estrae gli annunci.

Risultato: copertura di Chrono24 senza scraping, senza proxy, senza rotture.
"""
from __future__ import annotations

import email
import imaplib
import logging
import os
import re
from email.header import decode_header, make_header
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import extract
from ..models import Listing
from .base import BaseSource, SourceResult

log = logging.getLogger("radar.email")

# Il nome della fonte viene dedotto dal mittente, così sai da dove arriva.
SENDER_MAP = {
    "chrono24": "chrono24",
    "watchfinder": "watchfinder",
    "wristler": "wristler",
    "chronohunter": "chronohunter",
    "watchmaster": "watchmaster",
    "subito": "subito",
    "watchbox": "watchbox",
    "bezel": "bezel",
}

_SKIP_LINK = re.compile(
    r"unsubscribe|disiscriv|preferenz|privacy|terms|condizioni|help|support|"
    r"facebook|instagram|twitter|linkedin|youtube|apps?\.apple|play\.google|"
    r"/account|/login|/faq|mailto:|tel:",
    re.I,
)


class EmailSource(BaseSource):

    def collect(self) -> SourceResult:
        host = os.environ.get(self.cfg.get("imap_host_env", "IMAP_HOST"), "")
        user = os.environ.get(self.cfg.get("imap_user_env", "IMAP_USER"), "")
        pwd = os.environ.get(self.cfg.get("imap_pass_env", "IMAP_PASS"), "")

        if not (host and user and pwd):
            return SourceResult(self.name, False, [], "credenziali IMAP non configurate")

        listings: list[Listing] = []
        try:
            with imaplib.IMAP4_SSL(host) as imap:
                imap.login(user, pwd)
                imap.select(self.cfg.get("mailbox", "INBOX"))
                criteria = "(UNSEEN)" if self.cfg.get("unseen_only", True) else "(ALL)"
                typ, data = imap.search(None, criteria)
                if typ != "OK":
                    return SourceResult(self.name, False, [], "ricerca IMAP fallita")

                ids = data[0].split()[-200:]      # cap di sicurezza
                for msg_id in ids:
                    typ, raw = imap.fetch(msg_id, "(RFC822)")
                    if typ != "OK" or not raw or not raw[0]:
                        continue
                    msg = email.message_from_bytes(raw[0][1])
                    sender = str(msg.get("From", "")).lower()
                    matched = self._match_sender(sender)
                    if not matched:
                        continue
                    subject = _decode(msg.get("Subject", ""))
                    body_html, body_text = _bodies(msg)
                    listings.extend(
                        self._parse_email(matched, subject, body_html, body_text)
                    )
                    if self.cfg.get("mark_seen", True):
                        imap.store(msg_id, "+FLAGS", "\\Seen")
        except imaplib.IMAP4.error as exc:
            return SourceResult(self.name, False, [], f"errore IMAP: {exc}")
        except Exception as exc:                       # pragma: no cover
            return SourceResult(self.name, False, [], f"{type(exc).__name__}: {exc}")

        return SourceResult(self.name, True, listings, f"{len(listings)} annunci da email")

    # ------------------------------------------------------------------

    def _match_sender(self, sender: str) -> str | None:
        for needle in self.cfg.get("from_contains", []):
            if needle.lower() in sender:
                return SENDER_MAP.get(needle.lower(), needle.lower())
        return None

    def _parse_email(self, source_name: str, subject: str,
                     body_html: str, body_text: str) -> Iterator[Listing]:
        wanted = self.ctx.config.references

        if body_html:
            soup = BeautifulSoup(body_html, "lxml")
            yield from self._parse_html_email(source_name, soup, wanted)
        elif body_text:
            yield from self._parse_text_email(source_name, subject, body_text, wanted)

    def _parse_html_email(self, source_name: str, soup: BeautifulSoup,
                          wanted: list[str]) -> Iterator[Listing]:
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if _SKIP_LINK.search(href):
                continue

            # il blocco annuncio è l'antenato che contiene testo + prezzo
            block = a
            for _ in range(5):
                if block.parent is None:
                    break
                block = block.parent
                text = block.get_text(" ", strip=True)
                if extract.matches_reference(None, text, wanted) and re.search(
                    r"[€$£]|\bEUR\b|\bUSD\b", text
                ):
                    break

            text = block.get_text(" ", strip=True)
            if not extract.matches_reference(None, text, wanted):
                continue

            clean = _clean_tracking(href)
            if clean in seen:
                continue
            seen.add(clean)

            img = block.find("img")
            title = a.get_text(" ", strip=True) or text[:160]

            yield Listing(
                source=source_name,
                url=clean,
                title=title[:200],
                raw_text=text[:4000],
                raw_price=_nearest_price(text),
                image=img["src"] if img and img.get("src", "").startswith("http") else None,
            )

    def _parse_text_email(self, source_name: str, subject: str,
                          body_text: str, wanted: list[str]) -> Iterator[Listing]:
        # email testuali: uno "blocco" per URL trovato
        for m in re.finditer(r"https?://\S+", body_text):
            url = _clean_tracking(m.group(0).rstrip(").,>"))
            if _SKIP_LINK.search(url):
                continue
            start = max(0, m.start() - 600)
            block = body_text[start:m.end() + 200]
            if not extract.matches_reference(None, block, wanted):
                continue
            yield Listing(
                source=source_name,
                url=url,
                title=subject[:200],
                raw_text=block,
                raw_price=_nearest_price(block),
            )


# =============================================================================
# helper
# =============================================================================

def _decode(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value or ""


def _bodies(msg) -> tuple[str, str]:
    html_parts, text_parts = [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
        except Exception:
            continue
        if ctype == "text/html":
            html_parts.append(decoded)
        elif ctype == "text/plain":
            text_parts.append(decoded)
    return "\n".join(html_parts), "\n".join(text_parts)


def _clean_tracking(url: str) -> str:
    """Toglie i parametri di tracciamento così la dedup funziona davvero."""
    url = re.sub(r"[?&](utm_[^=&]+|mc_[a-z]+|_hs[a-z]*|gclid|fbclid|"
                 r"cid|mkt_tok|ea_[a-z]+)=[^&]*", "", url, flags=re.I)
    return url.rstrip("?&")


_PRICE_NEAR = re.compile(
    r"(?:[€$£]|EUR|USD|GBP|CHF)\s?\d[\d.,\s']{2,12}|\d[\d.,\s']{2,12}\s?(?:[€$£]|EUR|USD|GBP|CHF)",
    re.I,
)


def _nearest_price(text: str) -> str | None:
    m = _PRICE_NEAR.search(text or "")
    return m.group(0) if m else None
