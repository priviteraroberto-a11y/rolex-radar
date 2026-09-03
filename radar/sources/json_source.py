"""Fonte JSON: legge il catalogo che il sito stesso usa per disegnarsi.

Perche' esiste
--------------
Universo Oro e' scritto in JavaScript: la pagina del catalogo, letta senza un
browser, mostra quattro orologi su centoventisette. Il sitemap non li elenca.
Sembrava una fonte inaccessibile.

Ma la pagina, per riempirsi, chiama un endpoint pubblico del suo stesso sito:
`/api/public/watches`. Quello risponde in JSON, senza chiavi ne' sessioni, ed
e' **meglio** di qualsiasi pagina HTML: marca, modello, referenza, prezzo,
anno, condizioni, scatola e documenti arrivano gia' separati, invece di dover
essere indovinati dal testo. Nessun prodotto correlato da tagliare, nessun
prezzo barrato da distinguere, nessuna vetrina che inquina l'anno.

Una richiesta per pagina, due in tutto: piu' leggero di una singola ricerca
HTML, e infinitamente piu' affidabile.

Come si configura
-----------------
    - name: universooro
      type: json
      start_urls: ["https://.../api/public/watches?page=1&limit=100"]
      items_path: items          # dove sta la lista dentro la risposta
      fields:
        title: "{brand} {model} {referenceNumber}"   # modello con segnaposto
        price: pricePublic                            # oppure nome di campo
        url: "https://.../orologi/{id}"

Un valore fra graffe e' un modello da riempire con i campi dell'elemento; un
valore senza graffe e' il nome di un campo da leggere cosi' com'e'.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from ..models import Listing
from .base import BaseSource, SourceResult

log = logging.getLogger("radar.json")


class JsonSource(BaseSource):

    def collect(self) -> SourceResult:
        listings: list[Listing] = []
        errori: list[str] = []
        pagine_ok = 0

        for url in self.cfg.get("start_urls", []):
            corpo, detail = self.ctx.fetcher.get(url)
            if corpo is None:
                errori.append(f"{url} → {detail}")
                continue
            try:
                dati = json.loads(corpo)
            except (json.JSONDecodeError, TypeError) as exc:
                errori.append(f"{url} → risposta non JSON: {exc}")
                continue

            pagine_ok += 1
            elementi = _scava(dati, self.cfg.get("items_path", "items"))
            if not isinstance(elementi, list):
                errori.append(f"{url} → '{self.cfg.get('items_path')}' non e' una lista")
                continue
            trovati = list(self._leggi(elementi))
            log.info("%s: %d annunci da %s", self.name, len(trovati), url)
            listings.extend(trovati)

        ok = pagine_ok > 0
        return SourceResult(self.name, ok, listings,
                            "; ".join(errori) if errori else "ok")

    # ------------------------------------------------------------------

    def _leggi(self, elementi: list) -> Iterator[Listing]:
        campi = self.cfg.get("fields", {}) or {}
        for e in elementi:
            if not isinstance(e, dict):
                continue
            url = _valore(e, campi.get("url"))
            if not url:
                continue

            l = Listing(
                source=self.name,
                url=str(url),
                title=str(_valore(e, campi.get("title")) or "")[:200],
                image=_assoluto(_valore(e, campi.get("image")), self.cfg),
            )

            prezzo = _numero(_valore(e, campi.get("price")))
            if prezzo:
                l.price_original = prezzo
                l.currency = str(self.cfg.get("currency", "EUR"))
                l.price_eur = prezzo if l.currency == "EUR" else None
                l.raw_price = f"{prezzo} {l.currency}"

            l.reference = _testo(_valore(e, campi.get("reference")))
            l.year = _intero(_valore(e, campi.get("year")))
            l.condition = _testo(_valore(e, campi.get("condition")))

            scatola = _valore(e, campi.get("box"))
            documenti = _valore(e, campi.get("papers"))
            if scatola is not None or documenti is not None:
                l.full_set = bool(scatola) and bool(documenti)

            disponibile = _valore(e, campi.get("available"))
            if disponibile is not None:
                atteso = str(self.cfg.get("available_value", "available")).lower()
                l.sold = str(disponibile).lower() != atteso

            # Il testo grezzo serve al riconoscimento, che lavora su stringhe.
            # Qui lo componiamo dai campi invece di raccoglierlo dalla pagina:
            # contiene solo questo orologio, e nient'altro.
            l.raw_text = " ".join(str(v) for v in e.values()
                                  if isinstance(v, (str, int, float)))[:4000]
            yield l


# =============================================================================
# helper
# =============================================================================

def _scava(dati: Any, percorso: str) -> Any:
    """`items` oppure `data.results`: segue il percorso puntato."""
    if not percorso:
        return dati
    for pezzo in percorso.split("."):
        if isinstance(dati, dict):
            dati = dati.get(pezzo)
        else:
            return None
    return dati


def _valore(elemento: dict, spec: Any) -> Any:
    """Legge un campo, oppure riempie un modello con piu' campi.

    "pricePublic"            -> il valore di quel campo
    "{brand} {model}"        -> i due campi uniti
    "https://x.it/p/{id}"    -> un indirizzo costruito
    """
    if spec is None:
        return None
    testo = str(spec)
    if "{" not in testo:
        return elemento.get(testo)
    fuori = []

    def riempi(pezzo: str) -> str:
        v = elemento.get(pezzo)
        if v is None:
            fuori.append(pezzo)
            return ""
        return str(v)

    risultato = ""
    resto = testo
    while "{" in resto:
        prima, _, dopo = resto.partition("{")
        chiave, _, resto = dopo.partition("}")
        risultato += prima + riempi(chiave.strip())
    risultato += resto
    # Un indirizzo con un buco dentro non e' un indirizzo.
    if fuori and testo.startswith("http"):
        return None
    return risultato.strip()


def _assoluto(valore: Any, cfg: dict) -> Any:
    if not valore:
        return None
    testo = str(valore)
    base = str(cfg.get("base_url", "")).rstrip("/")
    if testo.startswith("/") and base:
        return base + testo
    return testo


def _numero(v: Any) -> float | None:
    try:
        n = float(str(v).replace(",", "."))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _intero(v: Any) -> int | None:
    try:
        n = int(float(v))
        return n if 1900 <= n <= 2100 else None
    except (TypeError, ValueError):
        return None


def _testo(v: Any) -> str | None:
    t = str(v).strip() if v is not None else ""
    return t or None
