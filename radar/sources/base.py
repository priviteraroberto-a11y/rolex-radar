"""Contratto comune a tutte le fonti."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Listing

log = logging.getLogger("radar.fonte")


@dataclass
class SourceResult:
    name: str
    ok: bool
    listings: list[Listing]
    detail: str = ""


class BaseSource:
    def __init__(self, cfg: dict, ctx: Any):
        self.cfg = cfg
        self.ctx = ctx           # ha .fetcher e .config
        self.name = cfg.get("name", "unnamed")

    def collect(self) -> SourceResult:  # pragma: no cover - interfaccia
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Arricchimento dalle schede prodotto
    #
    # Sta qui, e non in una sola delle due fonti, perche' serve a tutte e due
    # e per la stessa ragione.
    #
    # Il catalogo — pagina o JSON che sia — da' l'elenco completo ma poco per
    # ciascuno: spesso solo titolo, prezzo e disponibilita'. Anno, condizione,
    # corredo e garanzia stanno nella scheda del singolo orologio, e sono
    # meta' del punteggio: quando sono spariti dalle fonti WooCommerce, un
    # Monaco e' passato da 92 punti a 43 in un giro.
    #
    # Da CHLW, per dire, il catalogo JSON ha la descrizione vuota su tutti e
    # 105 i prodotti, mentre la scheda dice "Referenza: 79360N, Anno: 2026,
    # Corredo: Nuovo Scatola e Garanzia Ufficiali".
    # ------------------------------------------------------------------

    def arricchisci(self, listings: list[Listing]) -> None:
        """Apre la scheda dei soli annunci che ci riguardano.

        Il setaccio e' volutamente grossolano — nome o referenza nel testo
        gia' raccolto — perche' un falso positivo costa una richiesta in piu',
        mentre un falso negativo costa un orologio.

        Il tetto non e' cortesia: da quando le fonti leggono il catalogo
        intero, una sola pagina puo' portare milleduecento schede, e aprirle
        tutte sarebbe un'ora di giro e un piccolo attacco al sito del negozio.
        """
        if not self.cfg.get("fetch_detail", True):
            return
        tetto = int(self.cfg.get("max_detail", 40))
        candidati = self._da_aprire(listings)
        if len(candidati) > tetto:
            log.warning("%s: %d schede pertinenti, ne apro %d (max_detail)",
                        self.name, len(candidati), tetto)
            candidati = candidati[:tetto]
        if candidati:
            log.info("%s: apro %d schede su %d annunci",
                     self.name, len(candidati), len(listings))
        for l in candidati:
            self._dalla_scheda(l)

    def _da_aprire(self, listings: list[Listing]) -> list[Listing]:
        """Quali schede vale la pena aprire.

        Di norma: quelle che nominano gia' un orologio seguito. Funziona
        finche' il catalogo dice abbastanza da riconoscerlo.

        Ma esistono cataloghi che non dicono niente, e li' questa regola si
        morde la coda: da CHLW la referenza sta SOLO nella scheda, quindi
        finche' non la apri l'annuncio non ti riguarda, e siccome non ti
        riguarda non la apri mai. Quattro orologi giusti — fra cui un Panerai
        PAM01538 sotto mercato — restavano invisibili.

        Con `detail_scope: brand` il setaccio diventa la marca: si aprono le
        schede di tutti i Tudor, Omega, Panerai e cosi' via. E' piu' largo di
        proposito, ed e' sostenibile solo su cataloghi piccoli — per questo
        non e' il comportamento di default, e per questo `max_detail` resta.
        Chi non ti interessa lo scarta il filtro dopo, come sempre.
        """
        if str(self.cfg.get("detail_scope", "orologio")) == "brand":
            marche = {str(w.brand).lower() for w in self.ctx.config.watches
                      if getattr(w, "brand", None)}
            return [l for l in listings
                    if any(m in (l.title or "").lower() for m in marche)]
        return [l for l in listings
                if self.ctx.config.riguarda_un_orologio(
                    f"{l.title} {l.raw_text or ''}")]

    def _dalla_scheda(self, listing: Listing) -> None:
        """Scarica la scheda prodotto: li' stanno anno, garanzia, corredo."""
        if not listing.url or listing.url.rstrip("/") in {
            str(u).rstrip("/") for u in self.cfg.get("start_urls", [])
        }:
            return
        html, _ = self.ctx.fetcher.get(listing.url)
        if not html:
            return
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        body = soup.get_text(" ", strip=True)[:8000]
        listing.raw_text = f"{listing.raw_text or ''} {body}".strip()
        if not listing.image:
            img = soup.find("meta", property="og:image")
            if img and img.get("content"):
                listing.image = urljoin(listing.url, img["content"])
