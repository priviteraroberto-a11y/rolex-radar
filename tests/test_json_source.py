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
    w = next(x for x in cfg.watches if x.id == "bb-chrono-flamingo")
    assert reject_reason(tudor, w) is None, reject_reason(tudor, w)
    assert tudor.seller_country == "IT"

    # e l'Hesalite, che non vuoi, resta fuori
    hesalite = next(l for l in trovati if "310.30" in l.title)
    extract.enrich(hesalite, src)
    ws = next(x for x in cfg.watches if x.id == "speedmaster")
    assert reject_reason(hesalite, ws) is not None
