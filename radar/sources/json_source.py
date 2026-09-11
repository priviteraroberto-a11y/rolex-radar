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

Poi si e' scoperto che quasi tutti i negozi ne hanno uno: Shopify espone
`/products.json`, WooCommerce espone `/wp-json/wc/store/v1/products`. Sono
cataloghi **interi**, non risultati di ricerca — ed e' questa la differenza
che conta, perche' una ricerca restituisce i primi dieci e gli altri li perde
in silenzio.

Come si configura
-----------------
    - name: universooro
      type: json
      start_urls: ["https://.../api/public/watches?page={page}&limit=100"]
      paginate: {da: 1, max: 20}   # si ferma da sola quando la pagina e' vuota
      items_path: items            # dove sta la lista dentro la risposta
      fields:
        title: "{brand} {model} {referenceNumber}"   # modello con segnaposto
        price: pricePublic                            # oppure nome di campo
        url: "https://.../orologi/{id}"

Un valore fra graffe e' un modello da riempire con i campi dell'elemento; un
valore senza graffe e' il nome di un campo da leggere cosi' com'e'. In tutti e
due i casi il nome puo' essere un percorso: `prices.price`, `variants.0.price`.

Le tre trappole trovate sui dati veri
-------------------------------------
1. **La scala del prezzo cambia da sito a sito.** WooCommerce restituisce il
   prezzo in centesimi su PlusWatch (`220000` = 2.200 €) e in euro interi su
   Bonanno (`23000` = 23.000 €). La distingue solo il campo
   `currency_minor_unit`, che sta dentro la risposta. Un divisore scritto a
   mano sarebbe sbagliato di cento volte su uno dei due — cioe' o una valanga
   di finti affari, o il silenzio totale.
   Per questo `price_scale_from` punta al campo, e non e' un numero.
2. **La descrizione e' HTML.** Zorzoli tiene referenza, anno, condizione e
   corredo dentro `body_html`, con i tag in mezzo. Vanno tolti, altrimenti il
   riconoscimento legge `<p>` invece di `4500V`.
3. **Il prezzo mancante non e' zero.** Meta' del catalogo di Bonanno ha prezzo
   0, che vuol dire "su richiesta". Zero passerebbe per l'affare del secolo.
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
from typing import Any, Iterator

from ..models import Listing
from . import pagine
from .base import BaseSource, SourceResult

log = logging.getLogger("radar.json")


class JsonSource(BaseSource):

    def collect(self) -> SourceResult:
        listings: list[Listing] = []
        errori: list[str] = []
        pagine_ok = 0
        visti: set[str] = set()

        for gruppo in pagine.serie(self.cfg):
            for url in gruppo:
                corpo, detail = self.ctx.fetcher.get(url)
                if corpo is None:
                    errori.append(f"{url} → {detail}")
                    break        # la pagina non risponde: inutile insistere
                try:
                    dati = json.loads(corpo)
                except (json.JSONDecodeError, TypeError) as exc:
                    errori.append(f"{url} → risposta non JSON: {exc}")
                    break

                elementi = _scava(dati, self.cfg.get("items_path", "items"))
                if not isinstance(elementi, list):
                    errori.append(
                        f"{url} → '{self.cfg.get('items_path')}' non e' una lista")
                    break

                pagine_ok += 1
                if not elementi:
                    break        # pagina vuota: questo catalogo e' finito

                trovati = [l for l in self._leggi(elementi) if l.url not in visti]
                visti.update(l.url for l in trovati)
                log.info("%s: %d annunci da %s", self.name, len(trovati), url)
                listings.extend(trovati)

        log.info("%s: %d annunci in totale su %d pagine",
                 self.name, len(listings), pagine_ok)
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

            descrizione = self._descrizione(e, campi)

            l = Listing(
                source=self.name,
                url=str(url),
                title=_pulisci(_valore(e, campi.get("title")))[:200],
                image=_assoluto(_valore(e, campi.get("image")), self.cfg),
            )

            prezzo = self._prezzo(e, campi)
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
            # contiene solo questo orologio, e nient'altro. Niente prodotti
            # correlati, niente vetrina, niente eco della ricerca.
            l.raw_text = " ".join(
                x for x in (l.title, descrizione, _scalari(e)) if x
            )[:4000]
            yield l

    # ------------------------------------------------------------------

    def _descrizione(self, e: dict, campi: dict) -> str:
        """Il testo lungo della scheda, ripulito dai tag."""
        spec = campi.get("description")
        if not spec:
            return ""
        pezzi = spec if isinstance(spec, list) else [spec]
        return " ".join(_senza_tag(_valore(e, p)) for p in pezzi).strip()

    def _prezzo(self, e: dict, campi: dict) -> float | None:
        """Il prezzo, riportato alla scala giusta.

        `price_scale_from` punta al campo che dice quante cifre decimali sono
        gia' dentro il numero. Se il campo manca la scala e' 1: meglio un
        prezzo grezzo e visibilmente assurdo che uno diviso a caso.
        """
        grezzo = _numero(_valore(e, campi.get("price")))
        if grezzo is None:
            return None
        scala = self.cfg.get("price_scale_from")
        if scala:
            cifre = _intero_semplice(_valore(e, scala))
            if cifre:
                grezzo = grezzo / (10 ** cifre)
        return grezzo or None


# =============================================================================
# helper
# =============================================================================

def _scava(dati: Any, percorso: str) -> Any:
    """`items`, `data.results`, `variants.0.price`: segue il percorso puntato."""
    if not percorso:
        return dati
    for pezzo in str(percorso).split("."):
        if isinstance(dati, dict):
            dati = dati.get(pezzo)
        elif isinstance(dati, list):
            if not pezzo.lstrip("-").isdigit():
                return None
            i = int(pezzo)
            dati = dati[i] if -len(dati) <= i < len(dati) else None
        else:
            return None
    return dati


def _valore(elemento: dict, spec: Any) -> Any:
    """Legge un campo, oppure riempie un modello con piu' campi.

    "pricePublic"            -> il valore di quel campo
    "variants.0.price"       -> il valore in fondo al percorso
    "{brand} {model}"        -> i due campi uniti
    "https://x.it/p/{id}"    -> un indirizzo costruito
    """
    if spec is None:
        return None
    testo = str(spec)
    if "{" not in testo:
        return _scava(elemento, testo)
    fuori = []

    def riempi(pezzo: str) -> str:
        v = _scava(elemento, pezzo)
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


def _scalari(e: dict) -> str:
    """I valori semplici dell'elemento, per il riconoscimento.

    Solo il primo livello e solo stringhe e numeri: le liste annidate
    porterebbero dentro varianti, immagini e categorie di tutto il negozio.
    Le stringhe che sembrano HTML sono ripulite: `body_html` finisce qui, e
    senza pulizia il testo grezzo sarebbe per meta' fatto di tag.
    """
    pezzi = []
    for v in e.values():
        if isinstance(v, str):
            pezzi.append(_senza_tag(v) if "<" in v else v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            pezzi.append(str(v))
    return " ".join(pezzi)


def _senza_tag(v: Any) -> str:
    """Toglie i tag e riporta le entita' al loro carattere."""
    if not v:
        return ""
    testo = re.sub(r"<[^>]+>", " ", str(v))
    return re.sub(r"\s+", " ", _html.unescape(testo)).strip()


def _pulisci(v: Any) -> str:
    """I titoli WooCommerce arrivano con le entita' dentro: `ROLEX &#8211; 6827`."""
    return _senza_tag(v) if v is not None else ""


def _assoluto(valore: Any, cfg: dict) -> Any:
    if not valore:
        return None
    testo = str(valore)
    if testo.startswith("//"):
        return "https:" + testo
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


def _intero_semplice(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _testo(v: Any) -> str | None:
    t = str(v).strip() if v is not None else ""
    return t or None
