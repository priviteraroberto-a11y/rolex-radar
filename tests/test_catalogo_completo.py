"""Il catalogo intero, non i risultati di una ricerca.

Questi test nascono da un orologio perso. L'11/09 il Vacheron Overseas di
Zorzoli era in vetrina a 25.900 € e il radar rispondeva "nessun riscontro".
Non si era rotto niente: la fonte leggeva `search/suggest.json?...limit=10`, e
quell'annuncio arrivava undicesimo per pertinenza. Nessun errore da nessuna
parte — solo un orologio che per il sistema non esisteva.

I dati qui sotto sono copiati dalle risposte vere dei siti, l'11/09/2026, non
inventati. Ogni test corrisponde a un modo concreto di perdere un orologio.
"""
import json

from radar.config import Config
from radar.main import expand_urls, reject_reason
from radar.sources import pagine
from radar.sources.html_source import HtmlSource
from radar.sources.json_source import JsonSource


class _Fetcher:
    """Restituisce il corpo giusto per ogni indirizzo, e tiene il conto."""

    def __init__(self, per_url: dict, difetto=None):
        self.per_url = per_url
        self.difetto = difetto
        self.chiamate: list[str] = []

    def get(self, url):
        self.chiamate.append(url)
        if url in self.per_url:
            return self.per_url[url], "ok"
        return self.difetto, "ok" if self.difetto is not None else "404"


class _Ctx:
    def __init__(self, fetcher, config=None):
        self.fetcher = fetcher
        self.config = config or Config({"watches": []})


# =============================================================================
# Zorzoli — Shopify, il caso che ha fatto nascere tutto
# =============================================================================

ZORZOLI = json.dumps({"products": [
    {"id": 15574338928905, "title": "Audemars Piguet Royal Oak",
     "handle": "audemars-piguet-2",
     "body_html": "<p><strong>Cassa</strong> da 39 mm in acciaio, <strong>Vetro"
                  "</strong> zaffiro, <strong>Quadrante</strong> blu.</p>\n"
                  "<p><strong>Condizione:</strong> ottima</p>\n"
                  "<p><strong>Anno</strong>: 2013</p>\n"
                  "<p><strong>Referenza</strong>: 15202ST</p>\n"
                  "<p><strong>Corredo</strong>: completo  </p>",
     "vendor": "Zorzoli Orologi", "tags": ["Disponibile"],
     "variants": [{"price": "53900.00", "available": False}],
     "images": [{"src": "https://cdn.shopify.com/s/files/a.jpg?v=1"}]},
    {"id": 9497110479113, "title": "Vacheron Constantin Overseas",
     "handle": "vacheron-constantin",
     "body_html": "<p><strong>Cassa</strong> da 41mm in acciaio, <strong>Vetro"
                  "</strong> zaffiro, <strong>Quadrante</strong> blu.</p>\n"
                  "<p><strong>Condizione</strong>: ottima</p>\n"
                  "<p><strong>Anno</strong>: 2020</p>\n"
                  "<p><strong>Referenza</strong>: 4500v</p>\n"
                  "<p><strong>Corredo</strong>: Completo</p>",
     "vendor": "Vacheron Constantin", "tags": ["Disponibile"],
     "variants": [{"price": "25900.00", "available": True}],
     "images": [{"src": "https://cdn.shopify.com/s/files/b.jpg?v=1"}]},
]})

CFG_ZORZOLI = {
    "name": "zorzoli", "type": "json", "base_url": "https://zorzoliorologi.com",
    "start_urls": ["https://zorzoliorologi.com/collections/i-nostri-orologi"
                   "/products.json?limit=250&page={page}"],
    "paginate": {"max": 12},
    "items_path": "products", "available_value": "true",
    "fields": {
        "title": "title", "description": "body_html",
        "price": "variants.0.price", "available": "variants.0.available",
        "image": "images.0.src",
        "url": "https://zorzoliorologi.com/products/{handle}",
    },
}


def _zorzoli():
    prima = CFG_ZORZOLI["start_urls"][0].replace("{page}", "1")
    f = _Fetcher({prima: ZORZOLI}, difetto='{"products": []}')
    return JsonSource(CFG_ZORZOLI, _Ctx(f)).collect().listings, f


def test_overseas_letto_dal_catalogo():
    """L'annuncio che il radar aveva perso, con tutti i suoi dati."""
    annunci, _ = _zorzoli()
    o = next(a for a in annunci if "Overseas" in a.title)
    assert o.price_eur == 25900.0
    assert o.sold is False
    assert o.url == "https://zorzoliorologi.com/products/vacheron-constantin"


def test_referenza_e_anno_arrivano_dalla_descrizione():
    """Il titolo dice solo "Vacheron Constantin Overseas".

    Referenza, anno, condizione e corredo stanno dentro `body_html`, in mezzo
    ai tag. Senza ripulirli il riconoscimento leggerebbe `<strong>`.
    """
    annunci, _ = _zorzoli()
    o = next(a for a in annunci if "Overseas" in a.title)
    assert "4500v" in o.raw_text
    assert "2020" in o.raw_text
    assert "<p>" not in o.raw_text and "strong" not in o.raw_text


def test_le_schede_vendute_restano_vendute():
    """663 delle 716 schede di Zorzoli sono storico gia' venduto.

    E il tag dice "Disponibile" anche su quelle: l'unico campo che dice la
    verita' e' `variants.0.available`.
    """
    annunci, _ = _zorzoli()
    ap = next(a for a in annunci if "Royal Oak" in a.title)
    assert ap.sold is True


def test_l_overseas_passa_il_filtro_di_pertinenza():
    """La prova che conta: il Vacheron finisce davvero sotto `vc-overseas`."""
    cfg = Config({"watches": [{
        "id": "vc-overseas", "label": "Vacheron Overseas 41",
        "brand": "Vacheron Constantin",
        "references": ["4500V/110A-B128"],
        "reference_stems": ["4500V"],
    }]})
    annunci, _ = _zorzoli()
    o = next(a for a in annunci if "Overseas" in a.title)
    assert reject_reason(o, cfg.watches[0]) is None, reject_reason(o, cfg.watches[0])


def test_il_catalogo_si_ferma_quando_finisce():
    """`paginate.max` e' un tetto di sicurezza, non il numero di pagine lette."""
    _, f = _zorzoli()
    assert len(f.chiamate) == 2      # la prima piena, la seconda vuota e stop


# =============================================================================
# WooCommerce — la scala del prezzo cambia da negozio a negozio
# =============================================================================

def _woo(prezzo: str, minor_unit: int) -> str:
    return json.dumps([{
        "id": 1, "name": "Omega Speedmaster &#8211; 310.30.42.50.01.002",
        "permalink": "https://esempio.it/p/omega/", "sku": "",
        "prices": {"price": prezzo, "currency_code": "EUR",
                   "currency_minor_unit": minor_unit},
        "is_in_stock": True,
    }])


CFG_WOO = {
    "name": "prova", "type": "json",
    "start_urls": ["https://esempio.it/wp-json/wc/store/v1/products?page={page}"],
    "items_path": "", "available_value": "true",
    "price_scale_from": "prices.currency_minor_unit",
    "fields": {"title": "name", "price": "prices.price",
               "available": "is_in_stock", "reference": "sku",
               "url": "permalink"},
}


def _leggi_woo(corpo):
    prima = CFG_WOO["start_urls"][0].replace("{page}", "1")
    f = _Fetcher({prima: corpo}, difetto="[]")
    return JsonSource(CFG_WOO, _Ctx(f)).collect().listings


def test_prezzo_in_centesimi():
    """PlusWatch: `220000` con minor_unit 2 sono 2.200 €."""
    assert _leggi_woo(_woo("220000", 2))[0].price_eur == 2200.0


def test_prezzo_in_euro_interi():
    """Bonanno: `23000` con minor_unit 0 sono 23.000 €.

    Stesso software, stesso campo, scala diversa. Un divisore scritto a mano
    sbaglierebbe di cento volte su uno dei due negozi: o una valanga di finti
    affari, o il silenzio.
    """
    assert _leggi_woo(_woo("23000", 0))[0].price_eur == 23000.0


def test_prezzo_su_richiesta_non_e_un_affare():
    """370 schede su 800 da Bonanno hanno prezzo 0: vuol dire "su richiesta".

    Zero non e' un prezzo basso, e' un prezzo che manca. Preso alla lettera
    sarebbe l'occasione del secolo su ogni riga.
    """
    assert _leggi_woo(_woo("0", 0))[0].price_eur is None


def test_le_entita_html_nei_titoli():
    """WooCommerce scrive `ROLEX DATEJUST &#8211; 6827`."""
    assert "–" in _leggi_woo(_woo("100000", 2))[0].title


# =============================================================================
# La descrizione non e' un di piu': e' meta' del punteggio
# =============================================================================

_DESCR_PLUSWATCH = (
    "<p>    <title>Dettagli Orologio</title></p>\n"
    "<p>        body {<br />\n            font-family: Arial, sans-serif;<br />\n"
    "            margin: 20px;<br />\n            line-height: 1.6;<br />\n"
    "            width: 100%;<br />\n        }<br />\n        .title {<br />\n"
    "            font-size: 20px;<br />\n            color: black;<br />\n        }<br />\n"
    "        td {<br />\n            border-bottom: 1px solid #ddd;<br />\n        }<br />\n"
    "        tr:nth-child(even) {<br />\n            background-color: #f2f2f2;<br />\n        }</p>\n"
    "<p>Questo esemplare del <strong>2000</strong> &egrave; accompagnato dalla "
    "<strong>scatola originale e dai documenti originali</strong>.</p>\n"
    "<table><tbody>"
    "<tr><td><b>Anno di produzione</b></td><td>2000 (Anno approssimativo)</td></tr>"
    "<tr><td><b>Condizione</b></td><td>Usato (Ottimo)</td></tr>"
    "<tr><td><b>Corredo</b></td><td>Con scatola originale e documenti originali</td></tr>"
    "<tr><td><b>Warranty Country &#8211; Provenienza della Garanzia</b></td><td>Japan</td></tr>"
    "</tbody></table>")

CFG_PLUSWATCH = {
    "name": "pluswatch", "type": "json",
    "start_urls": ["https://www.pluswatch.it/wp-json/wc/store/v1/products?page={page}"],
    "items_path": "", "available_value": "true",
    "price_scale_from": "prices.currency_minor_unit",
    "fields": {"title": "name", "price": "prices.price",
               "available": "is_in_stock", "reference": "sku",
               "description": "description", "url": "permalink"},
}


def _pluswatch():
    dati = json.dumps([{
        "id": 1, "name": "Omega Constellation 1542.40 38mm",
        "permalink": "https://www.pluswatch.it/p/omega/", "sku": "",
        "is_in_stock": True,
        "prices": {"price": "220000", "currency_code": "EUR",
                   "currency_minor_unit": 2},
        "description": _DESCR_PLUSWATCH}])
    prima = CFG_PLUSWATCH["start_urls"][0].replace("{page}", "1")
    f = _Fetcher({prima: dati}, difetto="[]")
    return JsonSource(CFG_PLUSWATCH, _Ctx(f)).collect().listings[0]


def test_gli_attributi_arrivano_dalla_descrizione():
    """La regressione peggiore di questa serie di modifiche.

    Convertendo PlusWatch all'API avevo ridotto i campi scaricati per far
    scendere il peso da 4,6 MB a 52 KB per pagina. Fra i campi tagliati c'era
    `description`, e li' dentro stanno anno, condizione, corredo e provenienza
    della garanzia — cioe' meta' di quello che il punteggio guarda.

    Il giorno dopo, in produzione, un Monaco che valeva 92 punti ne valeva 43.
    Non era cambiato il prezzo e non era cambiato il mercato: era sparito tutto
    cio' che il sistema sapeva di quell'orologio.
    """
    from radar import extract
    l = _pluswatch()
    extract.enrich(l)
    assert l.year == 2000
    assert l.condition == "excellent"
    assert l.full_set is True
    assert l.warranty_region == "JP"


def test_il_css_non_mangia_il_testo_utile():
    """La scheda comincia con quattromila caratteri di regole di stile.

    WordPress butta i tag `<style>` ma lascia il loro contenuto come testo.
    Siccome il testo grezzo viene troncato a 4.000 caratteri e i dati veri
    stanno in fondo, tenere il CSS avrebbe voluto dire salvare **solo** il CSS
    e buttare via esattamente cio' per cui abbiamo scaricato la descrizione.
    """
    testo = _pluswatch().raw_text
    assert "font-family" not in testo and "#f2f2f2" not in testo
    assert "Anno di produzione" in testo and "Corredo" in testo
    assert len(testo) < 1500


def test_il_prezzo_in_centesimi_resta_giusto():
    assert _pluswatch().price_eur == 2200.0


# =============================================================================
# Conte Orologi — 1.227 schede in una pagina sola
# =============================================================================

CONTE = """<html><body><div class="row boxs_watchs">
<div class="box_orologio box_for_archive">
  <a href="https://www.conteorologi.it/orologi/omega-snoopy/"><img
     src="https://www.conteorologi.it/wp-content/uploads/2026/09/a.jpg"></a>
  <div class="cont_txt_box">
    <div class="titolo"><a href="https://www.conteorologi.it/orologi/omega-snoopy/">
      Omega Speedmaster Professional Moonwatch 'Silver Snoopy Award' New 2025</a></div>
    <small>REF: 310.32.42.50.02.001</small>
    <div class="tipoDisp" style="display:none">Disponibile</div>
  </div>
  <div class="prezzo"><span>&euro; 13.699</span></div>
</div>
</div></body></html>"""

CFG_CONTE = {
    "name": "conteorologi", "type": "html",
    "start_urls": ["https://www.conteorologi.it/orologi/"],
    "item_selector": ".box_orologio", "fetch_detail": False,
    "fields": {"title": ".titolo a, .titolo", "price": ".prezzo",
               "url": ".titolo a@href, a@href", "image": "img@src"},
}


def test_conte_legge_la_pagina_di_catalogo():
    """I selettori vecchi (`li.product`) non trovavano niente: tema su misura."""
    f = _Fetcher({"https://www.conteorologi.it/orologi/": CONTE})
    annunci = HtmlSource(CFG_CONTE, _Ctx(f)).collect().listings
    assert len(annunci) == 1
    a = annunci[0]
    assert a.url == "https://www.conteorologi.it/orologi/omega-snoopy/"
    assert "Snoopy" in a.title
    assert "13.699" in (a.raw_price or "")
    assert "310.32.42.50.02.001" in a.raw_text


def test_le_schede_si_aprono_solo_se_ci_riguardano():
    """Con il catalogo intero, aprirle tutte sarebbe un'ora di giro.

    La pagina di Conte ha 1.227 schede e dodici ci interessano. Il dettaglio
    va chiesto solo per quelle: qui il Rolex non deve costare una richiesta.
    """
    html = CONTE.replace("</div></body>", """
      <div class="box_orologio"><div class="titolo">
      <a href="https://www.conteorologi.it/orologi/rolex-datejust-6/">Rolex Datejust</a>
      </div><small>REF: 6827</small><div class="prezzo">€ 2.999</div></div>
      </div></body>""")
    cfg = Config({"watches": [{
        "id": "speedmaster-snoopy", "label": "Snoopy", "brand": "Omega",
        "references": ["310.32.42.50.02.001"]}]})
    f = _Fetcher({"https://www.conteorologi.it/orologi/": html}, difetto="<html></html>")
    conf = {**CFG_CONTE, "fetch_detail": True, "max_detail": 30}
    HtmlSource(conf, _Ctx(f, cfg)).collect()

    aperte = [u for u in f.chiamate if "/orologi/" in u and not u.endswith("/orologi/")]
    assert aperte == ["https://www.conteorologi.it/orologi/omega-snoopy/"]


# =============================================================================
# Le due lettere dopo la barra sono il materiale
# =============================================================================

def _vc(corpo: str, prezzo: str) -> list:
    dati = json.dumps({"products": [{
        "id": 1, "title": "Vacheron Constantin Overseas", "handle": "vc",
        "body_html": corpo, "vendor": "Vacheron Constantin",
        "variants": [{"price": prezzo, "available": True}],
        "images": [{"src": ""}]}]})
    prima = CFG_ZORZOLI["start_urls"][0].replace("{page}", "1")
    f = _Fetcher({prima: dati}, difetto='{"products": []}')
    return JsonSource(CFG_ZORZOLI, _Ctx(f)).collect().listings


def _config_vera():
    import yaml
    from pathlib import Path
    radice = Path(__file__).resolve().parent.parent
    return Config(yaml.safe_load((radice / "config.yaml").read_text(encoding="utf-8")))


def _orologio(id_: str):
    return next(w for w in _config_vera().watches if w.id == id_)


def test_le_due_generazioni_di_overseas_non_si_confondono():
    """27.700 € contro 34.000 €: un indice solo avrebbe mentito a entrambe."""
    vecchio = _vc("Acciaio 41mm. Anno : 2020 Referenza : 4500v", "25900.00")[0]
    nuovo = _vc("Acciaio 41mm. Anno : 2025 Referenza : 4520V/210A-B128", "31000.00")[0]
    assert reject_reason(vecchio, _orologio("vc-overseas-4500")) is None
    assert reject_reason(nuovo, _orologio("vc-overseas-4520")) is None
    assert reject_reason(vecchio, _orologio("vc-overseas-4520")) is not None
    assert reject_reason(nuovo, _orologio("vc-overseas-4500")) is not None


def test_l_overseas_in_oro_rosa_resta_fuori():
    """`210R` e' oro rosa e costa il doppio: 60.500 € contro 34.000.

    E' la stessa trappola del Royal Oak in oro, che una radice nuda aveva
    gia' fatto passare una volta.
    """
    oro = _vc("Oro rosa 41mm. Anno : 2025 Referenza : 4520V/210R-B967", "60500.00")[0]
    assert reject_reason(oro, _orologio("vc-overseas-4520")) is not None


def test_l_overseas_in_acciaio_passa_anche_senza_suffisso():
    """Meta' dei venditori scrive solo "4520V". Non e' un motivo per perderli."""
    nudo = _vc("Acciaio 41mm. Anno : 2025 Referenza : 4520V", "33000.00")[0]
    assert reject_reason(nudo, _orologio("vc-overseas-4520")) is None


# =============================================================================
# Quattro quadranti, un titolo solo
# =============================================================================

def _tudor(corpo: str, prezzo: str):
    dati = json.dumps({"products": [{
        "id": 2, "title": "Tudor Black Bay Chrono", "handle": "t",
        "body_html": corpo, "vendor": "Tudor",
        "variants": [{"price": prezzo, "available": True}],
        "images": [{"src": ""}]}]})
    prima = CFG_ZORZOLI["start_urls"][0].replace("{page}", "1")
    f = _Fetcher({prima: dati}, difetto='{"products": []}')
    return JsonSource(CFG_ZORZOLI, _Ctx(f)).collect().listings[0]


def test_il_quadrante_giallo_esce():
    """8.000 € contro 4.330 di mediana: da solo spostava l'indice del Panda."""
    giallo = _tudor("Acciaio 41 mm, Quadrante opalino con contatori giallo. "
                    "Anno: 2026 Referenza: 79360N", "8000.00")
    assert reject_reason(giallo, _orologio("bb-chrono-panda")) is not None


def test_ma_il_panda_resta():
    """Il Panda ha il quadrante opalino davvero.

    Escludere la parola "opalino" — che e' quella scritta nell'annuncio del
    giallo — avrebbe cancellato proprio l'orologio cercato, e nei log sarebbe
    sembrato che il mercato fosse vuoto.
    """
    panda = _tudor("Acciaio 41 mm, Quadrante opalino con contatori neri. "
                   "Anno: 2024 Referenza: 79360N", "4200.00")
    assert reject_reason(panda, _orologio("bb-chrono-panda")) is None, \
        reject_reason(panda, _orologio("bb-chrono-panda"))


def test_il_fuori_produzione_ha_la_sua_tabella_degli_anni():
    """La tabella generale e' centrata sul 2024, che per un fuori produzione
    e' un anno che non esiste.

    Il 4500V ha smesso di essere prodotto: l'esemplare tipico e' un 2021, e
    l'indice misura proprio quelli. Con la tabella generale ogni 4500V
    prendeva `_default` 0,89 e usciva "caro" contro un indice fatto di
    esemplari come lui — cioe' nessuna notifica, mai, su questo modello.
    """
    anni = _orologio("vc-overseas-4500").get("fair_value.multipliers.year")
    assert anni[2021] == 1.00
    # il valore generale per "vecchio" non deve sopravvivere alla fusione
    assert anni["_default"] != 0.89
    # e le chiavi restano numeriche: "2020" e 2020 sarebbero due chiavi diverse
    assert all(k == "_default" or isinstance(k, int) for k in anni)
    # l'orologio ancora in produzione tiene la tabella generale
    assert _orologio("vc-overseas-4520").get(
        "fair_value.multipliers.year")["_default"] == 0.89


def test_le_parole_del_titolo_non_guardano_la_descrizione():
    """La deroga vale solo per `exclude_in_text`, non per tutte le esclusioni.

    Se `exclude_keywords` guardasse anche il corpo, un annuncio che nomina di
    sfuggita un altro modello sparirebbe — ed e' il motivo per cui guarda solo
    il titolo.
    """
    w = _orologio("bb-chrono-panda")
    assert "Flamingo" in w.exclude_keywords
    assert "Flamingo" not in (w.exclude_in_text or [])
    accanto = _tudor("Quadrante opalino con contatori neri. Referenza: 79360N. "
                     "Disponibile anche la versione Flamingo.", "4200.00")
    assert reject_reason(accanto, w) is None


# =============================================================================
# Land-Dweller — la parola "oro" qui e' una trappola
# =============================================================================

def _annuncio(titolo: str, prezzo: float, anno: int, corpo: str = ""):
    from radar.models import Listing
    return Listing(source="chrono24", url="https://www.chrono24.it/rolex/x--id1.htm",
                   title=titolo, raw_text=f"{titolo} {corpo}",
                   price_eur=prezzo, year=anno)


def test_acciaio_e_oro_bianco_non_e_un_orologio_in_oro():
    """Il Land-Dweller in acciaio ha la lunetta in oro bianco davvero.

    Mezzo mercato lo scrive nel titolo: "Stahl/Weissgold", "Oystersteel and
    white gold", "acciaio e oro bianco". Mettere "oro" o "gold" fra le parole
    escluse — come si fa per il Royal Oak, dove serve — qui avrebbe cancellato
    ogni annuncio giusto. E' la stessa forma del "quadrante opalino".
    """
    for titolo in [
        "Rolex Land-Dweller 40 Ref. 127334 Stahl/Weissgold 2026 Full Set",
        "Rolex Land-Dweller 40mm Oystersteel and white gold 127334",
        "Rolex Land-Dweller 40 - acciaio e oro bianco - Referenza 127334",
    ]:
        l = _annuncio(titolo, 22950, 2026)
        assert reject_reason(l, _orologio("land-dweller-40")) is None, titolo


def test_everose_e_platino_restano_fuori():
    oro = _annuncio("Rolex Land-Dweller 40 Everose 127336", 48000, 2026)
    plat = _annuncio("Rolex Land-Dweller 127286TBR platino diamanti", 95000, 2026)
    assert reject_reason(oro, _orologio("land-dweller-40")) is not None
    assert reject_reason(plat, _orologio("land-dweller-40")) is not None


def test_le_due_misure_non_si_mescolano():
    """23.250 € contro 18.400: sono due mercati, come i due Overseas."""
    q40 = _annuncio("Rolex Land-Dweller 40 127334 Full Set", 23000, 2026)
    q36 = _annuncio("Rolex Land-Dweller 36 127234 Unworn", 17590, 2026)
    assert reject_reason(q40, _orologio("land-dweller-40")) is None
    assert reject_reason(q36, _orologio("land-dweller-36")) is None
    assert reject_reason(q40, _orologio("land-dweller-36")) is not None
    assert reject_reason(q36, _orologio("land-dweller-40")) is not None


def test_il_titolo_senza_referenza_si_salva_col_corpo():
    """Un annuncio su cinque ha un titolo che non dice ne' misura ne' referenza.

    Verificato su Chrono24: quello intitolato solo "Rolex Land-Dweller" porta
    127234 quattro volte nel corpo della scheda. Per questo il riconoscimento
    guarda anche li', e per questo non serve allentare il filtro — allentarlo
    avrebbe rimesso insieme i due mercati che stiamo tenendo separati.
    """
    l = _annuncio("Rolex Land-Dweller", 18000, 2026,
                  corpo="Referenza 127234 Oyster 36 mm acciaio Oystersteel e oro bianco")
    assert reject_reason(l, _orologio("land-dweller-36")) is None


def test_il_222_in_oro_giallo_resta_fuori():
    """Solo acciaio. E l'oro si toglie con la referenza, non con le parole.

    Su 10 annunci in oro trovati in UE il 12/09, **quattro non nominano il
    materiale nel titolo**: si chiamano "Historiques 222" e costano 66.720,
    71.800 e 86.800 euro. Un filtro a parole ne avrebbe presi sei su dieci e
    avrebbe lasciato gli altri quattro dentro l'indice dell'acciaio, ad
    alzarlo di migliaia di euro.
    """
    w = _orologio("vc-222")
    oro = _annuncio("Vacheron Constantin Historiques 222", 79800, 2025,
                    corpo="Referenza 4200H/222J-B935 oro giallo 37mm")
    assert reject_reason(oro, w) is not None
    acciaio = _annuncio("Vacheron Constantin Historiques 222", 51950, 2025,
                        corpo="Referenza 4200H/222A-B934 acciaio 37mm")
    assert reject_reason(acciaio, w) is None, reject_reason(acciaio, w)


def test_il_222_senza_suffisso_passa_lo_stesso():
    """Meta' degli annunci scrive solo "222" o "4200H".

    Cercare `4200H/222A` invece di `4200H` faceva scendere i risultati da 119
    a 66: erano acciai veri, al prezzo giusto, che semplicemente non
    scrivevano il suffisso.
    """
    l = _annuncio("Vacheron Constantin Historiques 222 Blue Dial 4200H NEW",
                  51950, 2025)
    assert reject_reason(l, _orologio("vc-222")) is None


def test_oro_non_e_una_parola_da_escludere():
    """Fra le fonti c'e' un negozio che si chiama Universo Oro.

    Mettere "Oro" fra le parole escluse sembra la scorciatoia ovvia per
    togliere l'oro giallo, e taglierebbe fuori un intero negozio per il suo
    nome. E' la stessa famiglia di errore di "opalino" sul Tudor e di "oro
    bianco" sul Land-Dweller.
    """
    escluse = [k.lower() for k in _orologio("vc-222").exclude_keywords]
    assert "oro" not in escluse and "gold" not in escluse


def test_il_prezzo_di_mercato_non_e_un_affare():
    """La taratura dei moltiplicatori, provata dove faceva piu' danno.

    Su 87 annunci di Land-Dweller quasi tutti sono nuovi del 2026: l'esemplare
    tipico di questo mercato e' proprio quello, e l'indice misura lui. Con le
    tabelle generali — 1,12 per il 2026, 1,11 per il "nuovo" — un esemplare
    normalissimo a prezzo pieno usciva col valore equo gonfiato del 24%, cioe'
    81 punti su 100 e una notifica su Telegram. Una raffica di falsi affari,
    che e' il modo piu' rapido per rendere inutile il sistema.
    """
    from radar.fairvalue import FairValueEngine
    from radar.models import Listing
    w = _orologio("land-dweller-40")
    indice = 22958.0
    comps = [{"price_eur": indice} for _ in range(40)]
    eng = FairValueEngine(w, comps)
    a_mercato = Listing(source="x", url="https://www.chrono24.it/rolex/a--id1.htm",
                        title="Land-Dweller 40", price_eur=indice,
                        year=2026, condition="new", full_set=True)
    eng.evaluate(a_mercato)
    assert abs(a_mercato.delta_pct or 0) < 3, \
        f"un esemplare tipico a prezzo di mercato risulta {a_mercato.delta_pct:+.1f}%"


def test_gli_altri_rolex_non_entrano():
    """Il Datejust 126334 assomiglia alla referenza e non c'entra niente."""
    dj = _annuncio("Rolex Datejust 41 126334 acciaio e oro bianco", 11000, 2024)
    assert reject_reason(dj, _orologio("land-dweller-40")) is not None


# =============================================================================
# Orologi Famosi — link relativi e bollino "Venduto"
# =============================================================================

FAMOSI = """<html><head><base href="https://www.venditaorologiusati.it/"></head>
<body><div class="row">
<div class="col-lg-4 col-md-4 col-xs-12 marginbot-l">
 <div class="box"><div class="relative">
  <a href="scheda/omega-speedmaster-reduced-175.0032-del-1998-229">
   <img src="files/schede/229_0s.jpg" alt="Omega Speedmaster Reduced" class="w100">
   <div class="venduto"><img src="immagini/venduto.png" alt="Venduto"></div></a>
  </div>
  <div class="txtprod">
   <span class="carat1b"><strong>Omega Speedmaster Reduced 175.0032 del 1998</strong></span>
   <div class="carat1s">calibro 1140 automatico, cassa acciaio 39mm.</div>
   <div class="carat1x"><span><i class="fas fa-euro-sign"></i> 2.900,00</span></div>
  </div>
 </div>
</div></div></body></html>"""

CFG_FAMOSI = {
    "name": "orologifamosi", "type": "html",
    "start_urls": ["https://www.venditaorologiusati.it/catalogo/omega-2/1"],
    "item_selector": ".col-lg-4.marginbot-l, .col-md-4.marginbot-l",
    "fetch_detail": False,
    "fields": {"title": ".carat1b, .carat1b strong", "price": ".carat1x",
               "url": "a@href", "image": "img.w100@src"},
}


def _famosi():
    f = _Fetcher({CFG_FAMOSI["start_urls"][0]: FAMOSI})
    return HtmlSource(CFG_FAMOSI, _Ctx(f)).collect().listings


def test_i_link_relativi_seguono_il_base_dichiarato():
    """`<base href>` comanda sui link relativi.

    Qui i link sono scritti `scheda/omega-...`, senza barra. Risolti sulla
    pagina del catalogo verrebbe `/catalogo/scheda/omega-...`: un indirizzo
    che non esiste, e che per giunta comincia con `/catalogo` — quindi
    verrebbe scartato come "pagina di elenco". L'annuncio sparirebbe, o
    peggio finirebbe in dashboard con un link morto.
    """
    a = _famosi()[0]
    assert a.url == ("https://www.venditaorologiusati.it/scheda/"
                     "omega-speedmaster-reduced-175.0032-del-1998-229")


def test_il_bollino_venduto_e_dentro_un_immagine():
    """Qui "Venduto" non e' scritto: e' un `<img alt="Venduto">`.

    Il testo normale non lo vede. E siccome a essere venduto per primo e'
    quasi sempre quello a buon mercato, l'annuncio finirebbe in cima alla
    dashboard proprio perche' non e' piu' comprabile.
    """
    assert "Venduto" in _famosi()[0].raw_text


def test_il_prezzo_senza_simbolo_di_valuta():
    """Il simbolo dell'euro qui e' un'icona, non un carattere: "2.900,00"."""
    from radar import extract
    assert extract.parse_price(_famosi()[0].raw_price)[0] == 2900.0


# =============================================================================
# Le due trappole che avrebbero azzerato tutto in silenzio
# =============================================================================

def test_page_sopravvive_alla_ricerca():
    """`expand_urls` riempiva i segnaposto con `str.format()`.

    `format()` pretende di conoscerli tutti: davanti a `{page}` sollevava
    KeyError, l'indirizzo veniva scartato con un warning nei log e il catalogo
    spariva. Cioe' esattamente il difetto che stiamo togliendo, reintrodotto
    dal lato opposto.
    """
    cfg = Config({"watches": [{"id": "x", "label": "X",
                               "references": ["4500V"],
                               "search_terms": ["Overseas"]}]})
    fuori = expand_urls(
        {"name": "z", "start_urls": ["https://x.it/p.json?page={page}"]},
        cfg.watches[0])["start_urls"]
    assert fuori == ["https://x.it/p.json?page={page}"]


def test_un_catalogo_e_uguale_per_tutti_gli_orologi():
    """Senza `{q}` l'indirizzo non va moltiplicato per i termini di ricerca."""
    cfg = Config({"watches": [{"id": "x", "label": "X",
                               "references": ["A", "B", "C"]}]})
    fuori = expand_urls(
        {"name": "z", "start_urls": ["https://x.it/tutto.json"]},
        cfg.watches[0])["start_urls"]
    assert fuori == ["https://x.it/tutto.json"]


def test_la_ricerca_per_termine_funziona_ancora():
    cfg = Config({"watches": [{"id": "x", "label": "X", "references": ["A384"],
                               "search_terms": ["Zenith A384"]}]})
    fuori = expand_urls(
        {"name": "z", "start_urls": ["https://x.it/?s={q}"]},
        cfg.watches[0])["start_urls"]
    assert fuori == ["https://x.it/?s=Zenith%20A384"]


def test_i_cataloghi_restano_separati():
    """della Rocca ha sette pagine di marca indipendenti.

    Se fossero un elenco piatto, la prima marca esaurita fermerebbe anche le
    sei successive.
    """
    gruppi = pagine.serie({"start_urls": ["https://x.it/a/{page}",
                                          "https://x.it/b/{page}",
                                          "https://x.it/fisso"],
                           "paginate": {"max": 3}})
    assert [len(g) for g in gruppi] == [3, 3, 1]
    assert gruppi[0][0] == "https://x.it/a/1"


def test_il_tetto_delle_pagine_non_si_puo_alzare_a_piacere():
    """Un sito che risponde sempre la stessa cosa non deve tenere in ostaggio
    il giro."""
    gruppi = pagine.serie({"start_urls": ["https://x.it/{page}"],
                           "paginate": {"max": 10_000}})
    assert len(gruppi[0]) == pagine.MAX_PAGINE
