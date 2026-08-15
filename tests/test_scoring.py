"""Test del fair value e dello scoring — la logica che decide cosa ti arriva."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.config import Config          # noqa: E402
from radar.fairvalue import FairValueEngine  # noqa: E402
from radar.models import Listing         # noqa: E402
from radar.scorer import Scorer, stars   # noqa: E402

# Un orologio di prova, per non dipendere dalla configurazione di produzione.
_REALE = Config.load(Path(__file__).resolve().parent.parent / "config.yaml")
CFG = Config({**_REALE.raw, "watches": [{
    "id": "pepsi-test", "brand": "Rolex", "model": "GMT-Master II",
    "references": ["126710BLRO"],
    "model_keywords": ["GMT-Master", "GMT", "Pepsi"],
    "exclude_keywords": ["BLNR", "Batman"],
}]}).watches[0]


def mk(**kw) -> Listing:
    base = dict(source="t", url=f"https://t.it/{kw.get('url_id', 1)}",
                reference="126710BLRO", year=2025, condition="unworn",
                bracelet="jubilee", full_set=True, warranty_region="IT",
                never_polished=True, seller_trust=4)
    base.update({k: v for k, v in kw.items() if k != "url_id"})
    return Listing(**base)


# --- fair value ---------------------------------------------------------------

def test_seed_quando_mancano_dati():
    e = FairValueEngine(CFG, [])
    assert not e.data_driven
    assert e.index == CFG.get("fair_value.seed_price_eur")


def test_diventa_data_driven_con_abbastanza_campioni():
    comps = [{"price_eur": 32000 + i * 100, "year": 2025, "condition": "unworn",
              "bracelet": "jubilee", "full_set": 1, "warranty_region": "IT",
              "never_polished": 1} for i in range(20)]
    e = FairValueEngine(CFG, comps)
    assert e.data_driven
    assert e.n >= CFG.get("fair_value.min_samples")


def test_normalizzazione_rende_confrontabili_annunci_diversi():
    """Due annunci molto diversi ma coerenti col mercato devono dare indici vicini."""
    comps = [
        {"price_eur": 33000, "year": 2025, "condition": "unworn", "bracelet": "jubilee",
         "full_set": 1, "warranty_region": "IT", "never_polished": 1},
        {"price_eur": 28000, "year": 2023, "condition": "excellent", "bracelet": "jubilee",
         "full_set": 0, "warranty_region": "AE", "never_polished": 0},
    ] * 8
    e = FairValueEngine(CFG, comps)
    # la dispersione dei campioni normalizzati deve essere molto minore
    # di quella dei prezzi grezzi (33000 vs 28000 = 15%)
    spread = (max(e.samples) - min(e.samples)) / e.index
    assert spread < 0.15


def test_annuncio_peggiore_ha_fair_value_piu_basso():
    e = FairValueEngine(CFG, [])
    top = e.evaluate(mk())
    poor = e.evaluate(mk(year=2022, condition="good", bracelet="oyster",
                         full_set=False, warranty_region="AE", never_polished=False))
    assert poor.fair_value_eur < top.fair_value_eur


def test_rilevamento_sotto_mercato():
    e = FairValueEngine(CFG, [])
    l = e.evaluate(mk(price_eur=e.index * 0.85))
    assert l.delta_pct > 10
    assert e.is_underpriced(l)

    l2 = e.evaluate(mk(price_eur=e.index * 1.05))
    assert not e.is_underpriced(l2)


# --- scoring ------------------------------------------------------------------

def test_annuncio_perfetto_punteggio_alto():
    e = FairValueEngine(CFG, [])
    l = Scorer(CFG).score(e.evaluate(mk(price_eur=e.index * 0.9)))
    assert l.score >= 90, l.score_breakdown


def test_annuncio_mediocre_punteggio_basso():
    e = FairValueEngine(CFG, [])
    l = Scorer(CFG).score(e.evaluate(mk(
        price_eur=e.index * 1.25, year=2019, condition="good", bracelet="oyster",
        full_set=False, warranty_region="AE", never_polished=False, seller_trust=0,
    )))
    assert l.score < 40, l.score_breakdown


def test_2023_a_ottimo_prezzo_batte_2025_a_prezzo_pieno():
    """Il requisito chiave: niente filtri rigidi, il prezzo può compensare l'anno."""
    e = FairValueEngine(CFG, [])
    s = Scorer(CFG)

    occasione_2023 = s.score(e.evaluate(mk(
        year=2023, price_eur=e.index * 0.91 * 0.75, condition="mint",
    )))
    pieno_2025 = s.score(e.evaluate(mk(year=2025, price_eur=e.index * 1.15)))

    assert occasione_2023.score > pieno_2025.score, (
        occasione_2023.score_breakdown, pieno_2025.score_breakdown
    )


def test_garanzia_uae_penalizza_ma_non_esclude():
    e = FairValueEngine(CFG, [])
    s = Scorer(CFG)
    uae = s.score(e.evaluate(mk(warranty_region="AE", price_eur=e.index * 0.72)))
    assert uae.score > 60          # resta interessante, quindi ti arriva
    it = s.score(e.evaluate(mk(warranty_region="IT", price_eur=e.index * 0.72)))
    assert it.score > uae.score    # ma l'italiana vince a parità di prezzo


def test_prezzo_su_richiesta_penalizzato():
    e = FairValueEngine(CFG, [])
    s = Scorer(CFG)
    noprice = s.score(e.evaluate(mk(price_eur=None)))
    withprice = s.score(e.evaluate(mk(price_eur=e.index)))
    assert noprice.score < withprice.score


def test_annuncio_opaco_penalizzato():
    e = FairValueEngine(CFG, [])
    s = Scorer(CFG)
    opaco = s.score(e.evaluate(Listing(
        source="t", url="https://t.it/x", reference="126710BLRO",
        price_eur=e.index * 0.9,
    )))
    assert any("non dichiarate" in r for r in opaco.reasons)


def test_stelle():
    assert stars(96) == "★★★★★"
    assert stars(50) == "★★★☆☆"
    assert len(stars(0)) == 5


# =============================================================================
# GEOGRAFIA: dove si trova l'orologio
# =============================================================================

def test_italia_e_san_marino_valgono_di_piu():
    e = FairValueEngine(CFG, [])
    s = Scorer(CFG)
    prezzo = e.index * 0.95

    punteggi = {}
    for paese in ("IT", "SM", "EU", "US", "AE", None):
        l = mk(price_eur=prezzo, url_id=str(paese))
        l.seller_country = paese
        punteggi[paese] = s.score(e.evaluate(l)).score

    assert punteggi["IT"] == punteggi["SM"], "San Marino vale quanto l'Italia"
    assert punteggi["IT"] > punteggi["EU"] > punteggi["US"]
    assert punteggi["US"] >= punteggi["AE"]
    assert punteggi["EU"] > punteggi[None], "meglio saperlo che non saperlo"


def test_la_geografia_non_tocca_la_stima_di_valore():
    """Un Overseas non vale di più perché sta a Milano: è comodo, non prezioso."""
    e = FairValueEngine(CFG, [])
    vicino, lontano = mk(price_eur=30000.0), mk(price_eur=30000.0, url_id="2")
    vicino.seller_country, lontano.seller_country = "IT", "JP"
    e.evaluate(vicino); e.evaluate(lontano)
    assert vicino.fair_value_eur == lontano.fair_value_eur


def test_garanzia_e_posizione_sono_indipendenti():
    """Un orologio a Bologna con garanzia emiratina, e il caso opposto."""
    e = FairValueEngine(CFG, [])
    s = Scorer(CFG)

    qui_gar_estera = mk(price_eur=e.index, warranty_region="AE")
    qui_gar_estera.seller_country = "IT"
    lontano_gar_it = mk(price_eur=e.index, warranty_region="IT", url_id="2")
    lontano_gar_it.seller_country = "HK"

    a = s.score(e.evaluate(qui_gar_estera))
    b = s.score(e.evaluate(lontano_gar_it))
    assert a.score_breakdown["seller_location"] > b.score_breakdown["seller_location"]
    assert a.score_breakdown["warranty"] < b.score_breakdown["warranty"]


def test_san_marino_riconosciuto_nel_testo():
    from radar import extract
    assert extract.parse_location("Luogo: San Marino") == "SM"
    assert extract.normalize_region_group("SM") == "SM", \
        "San Marino non deve finire nel gruppo EU: non è nell'Unione"
