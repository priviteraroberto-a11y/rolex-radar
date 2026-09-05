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
    """L'indice rappresenta l'esemplare TIPICO del mercato, non quello
    perfetto: e' la mediana degli annunci osservati. Quindi il confronto va
    fatto con un esemplare tipico — 2024, ottime condizioni — altrimenti si
    misura la differenza fra "perfetto" e "medio" e la si scambia per uno
    sconto."""
    e = FairValueEngine(CFG, [])
    tipico = dict(year=2024, condition="excellent", never_polished=None)

    l = e.evaluate(mk(price_eur=e.index * 0.85, **tipico))
    assert l.delta_pct > 10
    assert e.is_underpriced(l)

    l2 = e.evaluate(mk(price_eur=e.index * 1.05, **tipico))
    assert not e.is_underpriced(l2)


def test_un_esemplare_perfetto_a_prezzo_medio_e_un_affare():
    """Conseguenza voluta del centrare i moltiplicatori sul tipico: un 2026
    mai indossato full set al prezzo della mediana vale piu' di quel prezzo,
    e il sistema deve dirlo. Prima lo giudicava "in linea" e non ti arrivava
    nessuna notifica."""
    e = FairValueEngine(CFG, [])
    perfetto = e.evaluate(mk(price_eur=e.index, year=2026, condition="unworn",
                             full_set=True, never_polished=True))
    assert perfetto.delta_pct > 8, perfetto.delta_pct


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

def test_la_geografia_non_e_un_punteggio():
    """Deve restare fuori dal punteggio: due annunci identici in paesi diversi
    valgono uguale. È il cancello sulle notifiche a fare la differenza."""
    e = FairValueEngine(CFG, [])
    s = Scorer(CFG)
    punteggi = set()
    for paese in ("IT", "SM", "EU", "US", "JP", None):
        l = mk(price_eur=e.index * 0.95, url_id=str(paese))
        l.seller_country = paese
        punteggi.add(s.score(e.evaluate(l)).score)
    assert len(punteggi) == 1, "il paese non deve spostare il punteggio"


def test_i_tre_piani_geografici():
    from radar.notify.decide import tier_geografico
    from radar.models import Listing

    def dove(paese):
        l = Listing(source="t", url="https://t/x")
        l.seller_country = paese
        return tier_geografico(l, CFG)

    assert dove("IT") == "home"
    assert dove("SM") == "home", "San Marino sta in primo piano come l'Italia"
    assert dove("EU") == "nearby"
    assert dove("CH") == "nearby"
    assert dove("JP") == "reference"
    assert dove("US") == "reference"
    assert dove(None) == "nearby", "senza paese si sbaglia per eccesso, non per difetto"


def test_il_resto_del_mondo_non_notifica_ma_resta_nei_dati():
    """Un affare a Singapore non ti sveglia, ma il suo prezzo conta."""
    from radar.notify.decide import decide_notifications
    e = FairValueEngine(CFG, [])
    s = Scorer(CFG)

    def annuncio(paese, sconto):
        l = mk(price_eur=e.index * sconto, url_id=paese)
        l.seller_country = paese
        return s.score(e.evaluate(l))

    italiano = annuncio("IT", 0.93)
    lontano = annuncio("SG", 0.70)      # affare clamoroso, ma dall'altra parte del mondo

    scelte = decide_notifications(
        [(italiano, {"is_new": True}), (lontano, {"is_new": True})],
        CFG, e.is_underpriced, force=True)

    paesi = [d.listing.seller_country for d in scelte]
    assert "IT" in paesi
    assert "SG" not in paesi, "il resto del mondo non deve notificare"
    # ma il suo prezzo è comunque stato valutato
    assert lontano.fair_value_eur and lontano.delta_pct > 20


def test_l_europa_ha_la_soglia_piu_alta():
    """Stesso annuncio, stesso punteggio: passa dall'Italia, non dall'Europa.

    Il punteggio è imposto a mano di proposito: qui si verifica la regola
    sulle soglie, non quanto vale un certo orologio.
    """
    from radar.notify.decide import decide_notifications
    e = FairValueEngine(CFG, [])

    def passa(paese, punteggio):
        l = mk(price_eur=e.index, url_id=f"{paese}{punteggio}")
        e.evaluate(l)
        l.score = punteggio
        l.seller_country = paese
        return bool(decide_notifications([(l, {"is_new": True})], CFG,
                                         lambda x, t=None: False, force=True))

    soglia_it = int(CFG.get("notifications.min_score"))
    soglia_eu = int(CFG.get("notifications.nearby_min_score"))
    assert soglia_eu > soglia_it

    intermedio = (soglia_it + soglia_eu) // 2
    assert passa("IT", intermedio), "in Italia un buon annuncio passa"
    assert not passa("EU", intermedio), "in Europa lo stesso annuncio non basta"
    assert passa("EU", soglia_eu + 2), "ma un annuncio eccellente passa comunque"


def test_a_parita_l_italia_arriva_prima():
    from radar.notify.decide import decide_notifications
    e = FairValueEngine(CFG, [])
    s = Scorer(CFG)

    ita = mk(price_eur=e.index * 0.90, url_id="it")
    ita.seller_country = "IT"
    eur = mk(price_eur=e.index * 0.90, url_id="eu")
    eur.seller_country = "EU"
    for l in (ita, eur):
        s.score(e.evaluate(l))

    scelte = decide_notifications(
        [(eur, {"is_new": True}), (ita, {"is_new": True})],
        CFG, e.is_underpriced, force=True)
    assert [d.listing.seller_country for d in scelte][0] == "IT"


def test_san_marino_riconosciuto_nel_testo():
    from radar import extract
    assert extract.parse_location("Luogo: San Marino") == "SM"
    assert extract.normalize_region_group("SM") == "SM", \
        "San Marino non deve finire nel gruppo EU: non è nell'Unione"


# --- confronto col listino ----------------------------------------------------

def _con_listino(prezzo_listino):
    from radar.config import Config as C
    return C({**_REALE.raw, "watches": [{
        "id": "x", "brand": "Rolex", "references": ["126710BLRO"],
        "fair_value": {"seed_price_eur": 20000, "list_price_eur": prezzo_listino},
    }]}).watches[0]


def test_lo_scarto_dal_listino_si_calcola():
    w = _con_listino(25000)
    e = FairValueEngine(w, [])
    l = e.evaluate(Listing(source="t", url="https://a/1", price_eur=20000.0))
    assert l.listino_eur == 25000
    assert l.delta_listino_pct == 20.0          # 20.000 e' il 20% sotto 25.000
    sopra = e.evaluate(Listing(source="t", url="https://a/2", price_eur=30000.0))
    assert sopra.delta_listino_pct == -20.0


def test_senza_listino_il_campo_resta_vuoto():
    """Meglio non dire niente che dire un numero inventato: molti di questi
    orologi sono fuori produzione e un listino attuale non esiste."""
    w = _con_listino(None)
    l = FairValueEngine(w, []).evaluate(
        Listing(source="t", url="https://a/3", price_eur=20000.0))
    assert l.listino_eur is None and l.delta_listino_pct is None


def test_il_listino_non_tocca_il_punteggio():
    """E' un'informazione, non un giudizio: due annunci identici devono avere
    lo stesso punteggio, che il listino sia noto o no."""
    dati = dict(source="t", url="https://a/4", price_eur=20000.0, year=2025,
                condition="excellent", full_set=True, seller_trust=4)
    con = Scorer(_con_listino(25000)).score(
        FairValueEngine(_con_listino(25000), []).evaluate(Listing(**dati)))
    senza = Scorer(_con_listino(None)).score(
        FairValueEngine(_con_listino(None), []).evaluate(Listing(**dati)))
    assert con.score == senza.score
