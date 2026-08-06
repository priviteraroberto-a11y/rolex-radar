"""Fonte HTML generica, guidata da selettori CSS definiti in config.yaml.

Aggiungere un dealer = aggiungere 8 righe di YAML. Nessun codice.

Strategia a tre livelli, dal più affidabile al più tollerante:
  1. JSON-LD (schema.org/Product) — molti e-commerce lo espongono già pronto
  2. selettori CSS dichiarati in config
  3. euristica: qualsiasi <a> il cui testo contiene la referenza cercata
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import extract
from ..models import Listing
from .base import BaseSource, SourceResult

log = logging.getLogger("radar.html")


class HtmlSource(BaseSource):

    def collect(self) -> SourceResult:
        listings: list[Listing] = []
        errors: list[str] = []
        pages_ok = 0

        for url in self.cfg.get("start_urls", []):
            html, detail = self.ctx.fetcher.get(url)
            if html is None:
                errors.append(f"{url} → {detail}")
                continue
            pages_ok += 1
            soup = BeautifulSoup(html, "lxml")

            found = list(self._from_jsonld(soup, url))
            if not found:
                found = list(self._from_selectors(soup, url))
            if not found:
                found = list(self._from_heuristic(soup, url))

            log.info("%s: %d annunci grezzi da %s", self.name, len(found), url)
            listings.extend(found)

        # arricchimento: se la pagina di dettaglio è raggiungibile, la leggiamo
        if self.cfg.get("fetch_detail", True):
            for l in listings:
                self._augment_from_detail(l)

        ok = pages_ok > 0
        detail = "; ".join(errors) if errors else "ok"
        return SourceResult(self.name, ok, listings, detail)

    # -- livello 1: JSON-LD ---------------------------------------------------

    def _from_jsonld(self, soup: BeautifulSoup, base_url: str) -> Iterator[Listing]:
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            for node in _walk_products(data):
                name = str(node.get("name", ""))
                desc = str(node.get("description", ""))
                url = node.get("url") or node.get("@id") or ""
                offers = node.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price = offers.get("price") or offers.get("lowPrice")
                currency = (offers.get("priceCurrency") or "EUR").upper()
                image = node.get("image")
                if isinstance(image, list):
                    image = image[0] if image else None
                if isinstance(image, dict):
                    image = image.get("url")

                if not (name or url):
                    continue

                l = Listing(
                    source=self.name,
                    url=urljoin(base_url, str(url)) if url else base_url,
                    title=name,
                    raw_text=f"{name} {desc}",
                    raw_price=str(price) if price is not None else None,
                    image=urljoin(base_url, image) if image else None,
                )
                if price is not None:
                    try:
                        l.price_original = float(price)
                        l.currency = currency
                        l.price_eur = extract.to_eur(l.price_original, currency)
                    except (TypeError, ValueError):
                        pass
                yield l

    # -- livello 2: selettori CSS --------------------------------------------

    def _from_selectors(self, soup: BeautifulSoup, base_url: str) -> Iterator[Listing]:
        item_sel = self.cfg.get("item_selector")
        fields = self.cfg.get("fields", {})
        if not item_sel:
            return
        for node in soup.select(item_sel):
            url = _pick(node, fields.get("url"), base_url)
            title = _pick(node, fields.get("title")) or node.get_text(" ", strip=True)[:200]
            price_txt = _pick(node, fields.get("price"))
            image = _pick(node, fields.get("image"), base_url)
            if not url:
                a = node.find("a", href=True)
                url = urljoin(base_url, a["href"]) if a else None
            if not url:
                continue
            yield Listing(
                source=self.name,
                url=url,
                title=title or "",
                raw_text=node.get_text(" ", strip=True)[:4000],
                raw_price=price_txt,
                image=image,
            )

    # -- livello 3: euristica -------------------------------------------------

    def _from_heuristic(self, soup: BeautifulSoup, base_url: str) -> Iterator[Listing]:
        """Ultima spiaggia: cerca il 'blocco scheda' attorno a ogni link.

        Un blocco scheda è l'antenato più piccolo di un link che (a) nomina la
        referenza cercata e (b) non contiene più di 3 link, perché altrimenti
        non è una scheda ma la griglia intera.
        """
        wanted = self.ctx.config.references
        seen: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            url = urljoin(base_url, href)
            if url in seen:
                continue

            block = None
            node = a
            for _ in range(5):
                if node.parent is None:
                    break
                node = node.parent
                text = node.get_text(" ", strip=True)
                if not extract.matches_reference(None, text, wanted):
                    continue
                if len(node.find_all("a", href=True)) > 3:
                    break                       # siamo risaliti troppo: è la griglia
                block = node
                break

            if block is None:
                continue
            seen.add(url)

            text = block.get_text(" ", strip=True)
            heading = block.find(["h1", "h2", "h3", "h4"])
            title = (heading.get_text(" ", strip=True) if heading
                     else a.get_text(" ", strip=True) or text)
            price_m = re.search(
                r"[€$£]\s?[\d.,\s']{3,}|[\d.,\s']{3,}\s?(?:[€$£]|EUR|CHF|USD|GBP)", text)
            img = block.find("img")

            yield Listing(
                source=self.name,
                url=url,
                title=title[:200],
                raw_text=text[:4000],
                raw_price=price_m.group(0) if price_m else None,
                image=urljoin(base_url, img["src"]) if img and img.get("src") else None,
            )

    # -- dettaglio ------------------------------------------------------------

    def _augment_from_detail(self, listing: Listing) -> None:
        """Scarica la scheda prodotto: lì stanno anno, garanzia, corredo."""
        if not listing.url or listing.url.rstrip("/") in {
            u.rstrip("/") for u in self.cfg.get("start_urls", [])
        }:
            return
        html, _ = self.ctx.fetcher.get(listing.url)
        if not html:
            return
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        body = soup.get_text(" ", strip=True)[:8000]
        listing.raw_text = f"{listing.raw_text} {body}"
        if not listing.image:
            img = soup.find("meta", property="og:image")
            if img and img.get("content"):
                listing.image = urljoin(listing.url, img["content"])


# =============================================================================
# helper
# =============================================================================

def _pick(node, selector: Optional[str], base_url: Optional[str] = None) -> Optional[str]:
    """Supporta 'sel1, sel2' e la sintassi 'sel@attributo'."""
    if not selector:
        return None
    for part in [s.strip() for s in selector.split(",") if s.strip()]:
        attr = None
        if "@" in part:
            part, attr = part.rsplit("@", 1)
        try:
            found = node.select_one(part.strip())
        except Exception:
            continue
        if not found:
            continue
        if attr:
            val = found.get(attr)
            if val:
                return urljoin(base_url, val) if base_url else val
        else:
            txt = found.get_text(" ", strip=True)
            if txt:
                return txt
    return None


def _walk_products(node, depth: int = 0):
    """Trova tutti i nodi @type Product/Offer in una struttura JSON-LD."""
    if depth > 6:
        return
    if isinstance(node, list):
        for item in node:
            yield from _walk_products(item, depth + 1)
    elif isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(str(x).lower() in ("product", "individualproduct") for x in types if x):
            yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from _walk_products(value, depth + 1)
