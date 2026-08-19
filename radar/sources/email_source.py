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
from datetime import date, timedelta
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
    r"/account|/login|/faq|mailto:|tel:|/cerca|/ricerca|/risultati",
    re.I,
)

# Chrono24 mette l'id dell'annuncio nel percorso: "...--id47847844.htm".
# Un link Chrono24 senza quell'id non e' un annuncio, e' un bottone del
# messaggio ("modifica ricerca salvata", "vedi tutti i risultati", il logo).
_LISTING_PATTERNS = {
    "chrono24": re.compile(r"-{1,2}id\d{4,}\b", re.I),
    "ebay": re.compile(r"/itm/\d+", re.I),
    "subito": re.compile(r"\.htm|/annunci/", re.I),
}


def _e_annuncio(url: str) -> bool:
    """Vero solo se l'URL punta a una singola pagina di annuncio.

    Serve una regola positiva, non solo una lista di esclusioni: il link
    "modifica ricerca salvata" di Chrono24 non contiene nessuna parola
    sospetta, e senza questo controllo veniva preso per un annuncio e
    abbinato al prezzo di quello accanto.
    """
    if _SKIP_LINK.search(url) or extract.e_pagina_di_servizio(url):
        return False
    for dominio, pattern in _LISTING_PATTERNS.items():
        if dominio in url.lower():
            return bool(pattern.search(url))
    return True


class EmailSource(BaseSource):

    def collect(self) -> SourceResult:
        host = os.environ.get(self.cfg.get("imap_host_env", "IMAP_HOST"), "")
        user = os.environ.get(self.cfg.get("imap_user_env", "IMAP_USER"), "")
        pwd = os.environ.get(self.cfg.get("imap_pass_env", "IMAP_PASS"), "")

        if not (host and user and pwd):
            return SourceResult(self.name, False, [], "credenziali IMAP non configurate")

        mailbox = self.cfg.get("mailbox", "INBOX")
        listings: list[Listing] = []
        # Diagnostica: senza questi numeri, "0 annunci" non distingue fra
        # "cartella vuota", "tutto gia letto" e "mittenti che non corrispondono".
        n_total = n_unseen = n_matched = 0
        other_senders: list[str] = []

        try:
            with imaplib.IMAP4_SSL(host) as imap:
                imap.login(user, pwd)

                typ, sel = imap.select(mailbox)
                if typ != "OK":
                    folders = self._list_folders(imap)
                    return SourceResult(
                        self.name, False, [],
                        f"cartella '{mailbox}' inesistente. Disponibili: {folders}",
                    )
                try:
                    n_total = int(sel[0])
                except (TypeError, ValueError, IndexError):
                    n_total = -1

                typ, unseen_data = imap.search(None, "(UNSEEN)")
                n_unseen = len(unseen_data[0].split()) if typ == "OK" and unseen_data[0] else 0

                criteria = self._criteri()
                typ, data = imap.search(None, criteria)
                if typ != "OK":
                    return SourceResult(self.name, False, [], "ricerca IMAP fallita")

                ids = data[0].split()[-200:]      # cap di sicurezza
                for msg_id in ids:
                    typ, raw = imap.fetch(msg_id, "(RFC822)")
                    if typ != "OK" or not raw or not raw[0]:
                        continue
                    msg = email.message_from_bytes(raw[0][1])
                    # Il campo From va DECODIFICATO prima del confronto: se il
                    # nome visualizzato contiene accenti, arriva come
                    # "=?UTF-8?B?...?=" e il nome del mittente sparisce.
                    sender = f"{_decode(msg.get('From', ''))} {msg.get('From', '')}".lower()
                    matched = self._match_sender(sender)
                    if not matched:
                        if len(other_senders) < 8:
                            other_senders.append(_decode(msg.get("From", ""))[:70])
                        continue
                    n_matched += 1
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

        finestra = self.cfg.get("since_days")
        detail = (f"cartella '{mailbox}': {n_total} messaggi"
                  + (f", ultimi {finestra} giorni" if finestra else "")
                  + f", {n_unseen} non letti, {n_matched} dai mittenti cercati, "
                  f"{len(listings)} annunci estratti")
        if other_senders:
            detail += " | mittenti scartati: " + ", ".join(other_senders)
        return SourceResult(self.name, True, listings, detail)

    def _criteri(self) -> str:
        """Quali messaggi guardare.

        Il filtro per DATA è preferibile a quello per "non letto": lo stato di
        lettura è fragile, basta aprire l'email dal telefono e il sistema non
        la vede più. La finestra temporale invece non dipende da cosa fai tu,
        e la deduplica per URL evita comunque di rinotificare due volte.
        """
        parti = []
        if self.cfg.get("unseen_only", False):
            parti.append("UNSEEN")
        giorni = self.cfg.get("since_days")
        if giorni:
            da = date.today() - timedelta(days=int(giorni))
            mesi = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
            parti.append(f"SINCE {da.day:02d}-{mesi[da.month - 1]}-{da.year}")
        return f"({' '.join(parti)})" if parti else "(ALL)"

    @staticmethod
    def _list_folders(imap) -> str:
        """Elenca le cartelle IMAP: serve quando il nome dell'etichetta è sbagliato."""
        try:
            typ, data = imap.list()
        except Exception:
            return "(elenco non disponibile)"
        if typ != "OK":
            return "(elenco non disponibile)"
        names = []
        for row in data or []:
            try:
                line = row.decode("utf-8", "replace")
            except AttributeError:
                line = str(row)
            if '"' in line:
                names.append(line.rsplit('"', 2)[-2])
        return ", ".join(n for n in names if n)[:400]

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
            if not _e_annuncio(href):
                continue

            block = self._blocco(a, wanted)
            if block is None:
                continue
            text = block.get_text(" ", strip=True)

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

    @staticmethod
    def _blocco(a, wanted):
        """Il piu' piccolo antenato che descrive *questo* annuncio e nessun altro.

        Il criterio decisivo non e' "contiene una referenza e un prezzo" — a
        forza di salire lo contiene anche il corpo intero del messaggio, e a
        quel punto il prezzo che si legge appartiene a un altro orologio. Il
        criterio e' il confine: appena il blocco contiene due link ad annunci,
        siamo saliti troppo e ci fermiamo. Se non troviamo mai un blocco che
        contiene un solo annuncio, meglio scartare il link che inventarsi un
        prezzo.
        """
        block = a
        for _ in range(6):
            if block.parent is None:
                return None
            block = block.parent
            annunci = [x for x in block.find_all("a", href=True)
                       if _e_annuncio(x["href"])]
            if len({x["href"].split("?")[0] for x in annunci}) > 1:
                return None
            text = block.get_text(" ", strip=True)
            if extract.matches_reference(None, text, wanted) and _HA_PREZZO.search(text):
                return block
        return None

    def _parse_text_email(self, source_name: str, subject: str,
                          body_text: str, wanted: list[str]) -> Iterator[Listing]:
        # email testuali: uno "blocco" per URL trovato
        for m in re.finditer(r"https?://\S+", body_text):
            url = _clean_tracking(m.group(0).rstrip(").,>"))
            if not _e_annuncio(url):
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
    """Toglie i parametri di tracciamento.

    Non serve alla deduplica (la chiave dell'annuncio ignora gia' tutto cio'
    che sta dopo il "?"), serve a te: il link che ti arriva su Telegram deve
    essere condivisibile e leggibile, non una riga di duecento caratteri con
    dentro l'identificativo della tua casella di posta.
    """
    if "chrono24" in url.lower() and _LISTING_PATTERNS["chrono24"].search(url):
        return url.split("?")[0]          # il percorso identifica gia' l'annuncio
    url = re.sub(r"[?&](utm_[^=&]+|mc_[a-z]+|_hs[a-z]*|gclid|fbclid|"
                 r"cid|mkt_tok|ea_[a-z]+|eeid|recid|ik[a-z]+|"
                 r"goal_[a-z_]+)=[^&]*", "", url, flags=re.I)
    url = re.sub(r"\?&+", "?", url)
    return url.rstrip("?&")


_HA_PREZZO = re.compile(r"[€$£]|\bEUR\b|\bUSD\b|\bCHF\b|\bGBP\b", re.I)

_PRICE_NEAR = re.compile(
    r"(?:[€$£]|EUR|USD|GBP|CHF)\s?\d[\d.,\s']{2,12}|\d[\d.,\s']{2,12}\s?(?:[€$£]|EUR|USD|GBP|CHF)",
    re.I,
)


def _nearest_price(text: str) -> str | None:
    m = _PRICE_NEAR.search(text or "")
    return m.group(0) if m else None
