"""La fonte JSON, provata sulla forma vera della risposta di Universo Oro."""
import json

import pytest

from radar.config import Config
from radar.main import reject_reason
from radar.models import Listing
from radar import extract
from radar.sources.json_source import JsonSource


RISPOSTA = json.dumps({
    "items": [
        {"id": "32129299-a13d-403b-9b1f-08f1b0d04acc", "brand": "Tudor",
         "model": "Black Bay Chrono", "referenceNumber": "79360N",
         "pricePublic": 4500, "condition": "very_good", "yearOfProduction": 2024,
         "primaryImage": "/objects/public/watches/1788444209836_81fdd3c7.webp",
         "hasBox": True, "hasPapers": True, "availability": "available"},
        {"id": "aaa", "brand": "Omega", "model": "Speedmaster Professional",
         "referenceNumber": "310.30.42.50.01.001", "pricePublic": 6700,
         "condition": "new", "yearOfProduction": 2026, "hasBox": True,
         "hasPapers": True, "availability": "available"},
        {"id": "bbb", "brand": "Rolex", "model": "Cosmograph Daytona",
         "referenceNumber": "116500LN", "pricePublic": 28000,
         "condition": "excellent", "yearOfProduction": 2023, "hasBox": True,
         "hasPapers": False, "availability": "sold"},
    ],
    "pagination": {"page": 1, "total": 3},
})

CFG = {
    "name": "universooro", "type": "json", "country": "IT", "dealer": True,
    "seller_trust": 4, "base_url": "https://www.universo-oro.it",
    "start_urls": ["https://www.universo-oro.it/api/public/watches?page=1"],
    "items_path": "items", "available_value": "available",
    "fields": {
        "title": "{brand} {model} {referenceNumber}", "reference": "referenceNumber",
        "price": "pricePublic", "year": "yearOfProduction", "condition": "condition",
        "box": "hasBox", "papers": "hasPapers", "available": "availability",
        "image": "primaryImage", "url": "https://www.universo-oro.it/orologi/{id}",
    },
}


class _Fetcher:
    def __init__(self, corpo): self.corpo = corpo
    def get(self, url): return self.corpo, "ok"


class _Ctx:
    def __init__(self, corpo):
        self.fetcher = _Fetcher(corpo)
        self.config = Config({"watches": [{"id": "x", "references": ["79360N"]}]})


@pytest.fixture
def annunci():
    return JsonSource(CFG, _Ctx(RISPOSTA)).collect().listings


def test_i_campi_arrivano_gia_separati(annunci):
    """E' il motivo per cui questa fonte vale piu' di una pagina HTML:
    niente da indovinare dal testo."""
    t = next(l for l in annunci if "Tudor" in l.title)
    assert t.price_eur == 4500.0
    assert t.year == 2024
    assert t.condition == "very_good"
    assert t.reference == "79360N"
    assert t.full_set is True
    assert t.sold is False
    assert t.url.endswith("32129299-a13d-403b-9b1f-08f1b0d04acc")
    assert t.image.startswith("https://www.universo-oro.it/objects/")


def test_il_titolo_si_compone_dai_campi(annunci):
    assert "Tudor Black Bay Chrono 79360N" in [l.title for l in annunci][0]


def test_il_venduto_viene_riconosciuto(annunci):
    daytona = next(l for l in annunci if "Daytona" in l.title)
    assert daytona.sold is True
    assert daytona.full_set is False      # scatola si', documenti no


def test_una_risposta_rotta_non_fa_esplodere_il_giro():
    r = JsonSource(CFG, _Ctx("non sono json")).collect()
    assert r.listings == [] and "non JSON" in r.detail
    r = JsonSource(CFG, _Ctx(json.dumps({"altro": 1}))).collect()
    assert r.listings == []


def test_gli_annunci_veri_passano_il_filtro_del_radar():
    """Il giro completo: quello che questa fonte produce deve superare i
    controlli come qualsiasi altro annuncio."""
    cfg = Config.load("config.yaml")
    src = next(s for s in cfg.get("sources") if s.get("name") == "universooro")
    trovati = JsonSource(src, _Ctx(RISPOSTA)).collect().listings
    tudor = next(l for l in trovati if "Tudor" in l.title)
    extract.enrich(tudor, src)
    assert tudor.seller_country == "IT"

    # Il titolo dice "Black Bay Chrono 79360N" e basta: e' il Panda, che costa
    # la meta' del Flamingo. Confonderli faceva sembrare un affare del 50% un
    # orologio a prezzo di mercato — l'errore che la rilevazione ha svelato.
    panda = next(x for x in cfg.watches if x.id == "bb-chrono-panda")
    flamingo = next(x for x in cfg.watches if x.id == "bb-chrono-flamingo")
    assert reject_reason(tudor, panda) is None, reject_reason(tudor, panda)
    assert reject_reason(tudor, flamingo) is not None

    # e l'Hesalite, che non vuoi, resta fuori
    hesalite = next(l for l in trovati if "310.30" in l.title)
    extract.enrich(hesalite, src)
    ws = next(x for x in cfg.watches if x.id == "speedmaster")
    assert reject_reason(hesalite, ws) is not None


# --- Zorzoli: Shopify, con i dati dentro la scheda ----------------------------

SHOPIFY = json.dumps({"resources": {"results": {"products": [
    {"available": False, "handle": "omega-speedmaster-doppio-zaffiro",
     "title": "Omega Speedmaster", "vendor": "Zorzoli Orologi", "price": "5200.00",
     "image": "https://cdn.shopify.com/x.jpg",
     "body": "<p><strong>Cassa</strong> da 42 mm, <strong>Vetro</strong> Hesalite."
             "</p><p><strong>Condizione</strong>: ottime condizioni</p>"
             "<p><strong>Anno</strong>: 2024</p>"
             "<p><strong>Referenza</strong>: 310.30.42.50.01.001</p>"
             "<p><strong>Corredo</strong>: Completo</p>"},
    {"available": True, "handle": "omega-speedmaster-9",
     "title": "Omega Speedmaster", "vendor": "Omega", "price": "6500.00",
     "image": "https://cdn.shopify.com/y.jpg",
     "body": "<p><strong>Condizione</strong>: ottima</p><p><strong>Anno</strong>: 2025</p>"
             "<p><strong>Referenza</strong>: 310.30.42.50.01.002</p>"
             "<p><strong>Corredo</strong>: Completo</p>"},
]}}})

ZORZOLI = {
    "name": "zorzoli", "type": "json", "country": "IT", "dealer": True,
    "seller_trust": 4, "base_url": "https://zorzoliorologi.com",
    "start_urls": ["https://zorzoliorologi.com/search/suggest.json?q=Speedmaster"],
    "items_path": "resources.results.products", "available_value": "true",
    "fields": {"title": "{vendor} {title}", "price": "price", "available": "available",
               "image": "image", "url": "https://zorzoliorologi.com/products/{handle}"},
}


def test_shopify_percorso_annidato_e_disponibilita_booleana():
    """`items_path` scende in tre livelli, e qui la disponibilita' e' true/false
    invece di una parola: meta' del catalogo Zorzoli e' gia' venduto."""
    trovati = JsonSource(ZORZOLI, _Ctx(SHOPIFY)).collect().listings
    assert len(trovati) == 2
    venduto = next(l for l in trovati if l.price_eur == 5200)
    disponibile = next(l for l in trovati if l.price_eur == 6500)
    assert venduto.sold is True and disponibile.sold is False
    assert disponibile.url.endswith("/products/omega-speedmaster-9")


def test_la_referenza_sta_nella_scheda_non_nel_titolo():
    """Zorzoli intitola tutto "Omega Speedmaster": referenza, anno e corredo
    stanno nel testo della scheda, ed e' li' che vanno letti."""
    from radar.config import Config
    cfg = Config.load("config.yaml")
    trovati = JsonSource(ZORZOLI, _Ctx(SHOPIFY)).collect().listings
    w = next(x for x in cfg.watches if x.id == "speedmaster")

    giusto = next(l for l in trovati if l.price_eur == 6500)
    extract.enrich(giusto, ZORZOLI)
    assert reject_reason(giusto, w) is None, reject_reason(giusto, w)
    assert giusto.year == 2025 and giusto.seller_country == "IT"

    # l'Hesalite (...01.001) resta fuori anche se la referenza e' solo nel corpo
    hesalite = next(l for l in trovati if l.price_eur == 5200)
    extract.enrich(hesalite, ZORZOLI)
    assert reject_reason(hesalite, w) is not None
