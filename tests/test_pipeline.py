"""Test end-to-end senza rete: fetcher finto, HTML realistico."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import dashboard, extract                    # noqa: E402
from radar.config import Config                         # noqa: E402
from radar.db import Database                           # noqa: E402
from radar.fairvalue import FairValueEngine             # noqa: E402
from radar.main import Context, filter_relevant         # noqa: E402
from radar.scorer import Scorer                         # noqa: E402
from radar.sources.html_source import HtmlSource        # noqa: E402

# I test non devono dipendere da QUALI orologi sono configurati in produzione:
# altrimenti si rompono ogni volta che ne aggiungi o togli uno. Prendiamo le
# impostazioni globali dal file vero (moltiplicatori, punteggi, soglie) e ci
# mettiamo sopra un orologio di prova definito qui.
_REALE = Config.load(Path(__file__).resolve().parent.parent / "config.yaml")

PEPSI_DI_PROVA = {
    "id": "pepsi-test", "brand": "Rolex", "model": "GMT-Master II",
    "nickname": "Pepsi", "references": ["126710BLRO"],
    "search_terms": ["126710"],
    "model_keywords": ["GMT-Master", "GMT Master", "GMT", "Pepsi"],
    "exclude_keywords": ["BLNR", "Batman", "Sprite", "Root Beer"],
    "fair_value": {"seed_price_eur": 31000},
}

CFG = Config({**_REALE.raw, "watches": [PEPSI_DI_PROVA]})
W = CFG.watches[0]


class FakeFetcher:
    """Serve pagine da un dizionario, così i test non toccano la rete."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if url in self.pages:
            return self.pages[url], "ok"
        return None, "404 (finto)"


LISTING_PAGE = """
<html><body>
  <ul class="products">
    <li class="product">
      <h2>Rolex GMT-Master II 126710BLRO Pepsi Jubilee 2025</h2>
      <span class="price">33.900 €</span>
      <a href="/orologio/pepsi-2025">vedi</a>
      <img src="/img/1.jpg">
    </li>
    <li class="product">
      <h2>Rolex GMT-Master II 126710BLRO Pepsi Oyster 2023</h2>
      <span class="price">28.400 €</span>
      <a href="/orologio/pepsi-2023">vedi</a>
    </li>
    <li class="product">
      <h2>Rolex Submariner 126610LN 2024</h2>
      <span class="price">12.900 €</span>
      <a href="/orologio/sub">vedi</a>
    </li>
  </ul>
</body></html>
"""

DETAIL_2025 = """
<html><head><meta property="og:image" content="https://d.it/big.jpg"></head>
<body><div class="desc">
  Rolex GMT-Master II ref. 126710BLRO, anno 2025. Unworn, mai indossato.
  Bracciale Jubilee. Full set: scatola, garanzia italiana e cartellino.
  Mai lucidato. Prezzo 33.900 €
</div></body></html>
"""

DETAIL_2023 = """
<html><body><div class="desc">
  Rolex GMT-Master II 126710BLRO, anno 2023. Ottime condizioni, cassa lucidata.
  Bracciale Oyster. Solo orologio, senza scatola. Warranty card UAE Dubai.
  Prezzo 28.400 €
</div></body></html>
"""

JSONLD_PAGE = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product",
 "name":"Rolex GMT-Master II 126710BLRO Pepsi Jubilee",
 "description":"Anno 2024, mint, full set, garanzia Italia, mai lucidato",
 "url":"https://dealer.it/p/pepsi-2024",
 "image":["https://dealer.it/p.jpg"],
 "offers":{"@type":"Offer","price":"31200","priceCurrency":"EUR"}}
</script></head><body></body></html>
"""

SRC_CFG = {
    "name": "demo", "type": "html", "enabled": True, "country": "IT",
    "dealer": True, "seller_trust": 4,
    "start_urls": ["https://demo.it/cerca"],
    "item_selector": "li.product",
    "fields": {"title": "h2", "price": ".price", "url": "a@href", "image": "img@src"},
}


def build_source(pages, cfg=SRC_CFG):
    return HtmlSource(cfg, Context(CFG, FakeFetcher(pages)))


# --- raccolta -----------------------------------------------------------------

def test_selettori_css_estraggono_gli_annunci():
    src = build_source({
        "https://demo.it/cerca": LISTING_PAGE,
        "https://demo.it/orologio/pepsi-2025": DETAIL_2025,
        "https://demo.it/orologio/pepsi-2023": DETAIL_2023,
        "https://demo.it/orologio/sub": "<html><body>Submariner</body></html>",
    })
    res = src.collect()
    assert res.ok
    assert len(res.listings) == 3          # anche il Submariner, filtrato dopo


def test_il_submariner_viene_scartato():
    src = build_source({
        "https://demo.it/cerca": LISTING_PAGE,
        "https://demo.it/orologio/pepsi-2025": DETAIL_2025,
        "https://demo.it/orologio/pepsi-2023": DETAIL_2023,
        "https://demo.it/orologio/sub": "<html><body>Rolex Submariner 126610LN</body></html>",
    })
    listings = [extract.enrich(l, SRC_CFG) for l in src.collect().listings]
    relevant = filter_relevant(listings, W)
    assert len(relevant) == 2
    assert all(l.reference == "126710BLRO" for l in relevant)


def test_dettaglio_arricchisce_i_campi():
    src = build_source({
        "https://demo.it/cerca": LISTING_PAGE,
        "https://demo.it/orologio/pepsi-2025": DETAIL_2025,
        "https://demo.it/orologio/pepsi-2023": DETAIL_2023,
        "https://demo.it/orologio/sub": "<html></html>",
    })
    listings = [extract.enrich(l, SRC_CFG) for l in src.collect().listings]
    by_year = {l.year: l for l in listings if l.year}

    a = by_year[2025]
    assert (a.price_eur, a.condition, a.bracelet) == (33900.0, "unworn", "jubilee")
    assert a.full_set is True and a.never_polished is True
    assert a.warranty_region == "IT"
    # l'immagine della lista ha la precedenza; og:image è solo un ripiego
    assert a.image == "https://demo.it/img/1.jpg"

    b = by_year[2023]
    assert (b.price_eur, b.bracelet) == (28400.0, "oyster")
    assert b.full_set is False and b.never_polished is False
    assert b.warranty_region == "AE"


def test_jsonld_ha_la_precedenza():
    src = build_source(
        {"https://dealer.it/cerca": JSONLD_PAGE},
        {**SRC_CFG, "name": "jsonld", "start_urls": ["https://dealer.it/cerca"],
         "fetch_detail": False},
    )
    res = src.collect()
    assert len(res.listings) == 1
    l = extract.enrich(res.listings[0], SRC_CFG)
    assert l.price_eur == 31200.0
    assert l.year == 2024 and l.condition == "mint" and l.warranty_region == "IT"


def test_fonte_irraggiungibile_non_esplode():
    src = build_source({})
    res = src.collect()
    assert not res.ok and res.listings == []
    assert "404" in res.detail


def test_euristica_quando_i_selettori_non_matchano():
    src = build_source(
        {"https://demo.it/cerca": LISTING_PAGE,
         "https://demo.it/orologio/pepsi-2025": DETAIL_2025,
         "https://demo.it/orologio/pepsi-2023": DETAIL_2023},
        {**SRC_CFG, "item_selector": "div.selettore-che-non-esiste", "fields": {}},
    )
    res = src.collect()
    # il fallback euristico trova comunque i link con la referenza
    assert len(res.listings) >= 1


# --- pipeline completa --------------------------------------------------------

def test_giro_completo(tmp_path):
    src = build_source({
        "https://demo.it/cerca": LISTING_PAGE,
        "https://demo.it/orologio/pepsi-2025": DETAIL_2025,
        "https://demo.it/orologio/pepsi-2023": DETAIL_2023,
        "https://demo.it/orologio/sub": "<html>Submariner</html>",
    })
    listings = filter_relevant(
        [extract.enrich(l, SRC_CFG) for l in src.collect().listings], CFG
    )

    db = Database(tmp_path / "h.db")
    engine = FairValueEngine(W, [])
    scorer = Scorer(W)

    for l in listings:
        engine.evaluate(l)
        scorer.score(l)
        db.upsert(l)

    db.save_market_snapshot("126710BLRO", engine.n, engine.median_raw,
                            engine.p25, engine.index)

    active = db.active_listings()
    assert len(active) == 2
    assert all(0 <= a["score"] <= 100 for a in active)

    market = engine.summary()
    market["label"] = "Rolex GMT-Master II “Pepsi”"
    out = dashboard.build(db, [market], tmp_path / "index.html")
    html = out.read_text(encoding="utf-8")
    assert "Pepsi" in html
    assert "33.900" in html and "28.400" in html
    assert "{" not in html.split("<script>")[0].split("<style>")[0]  # niente placeholder
    db.close()


def test_dashboard_gestisce_database_vuoto(tmp_path):
    db = Database(tmp_path / "v.db")
    engine = FairValueEngine(W, [])
    out = dashboard.build(db, engine.summary(), tmp_path / "index.html")
    assert "RADAR OROLOGI" in out.read_text(encoding="utf-8")
    db.close()


# --- motivi di scarto ---------------------------------------------------------

def test_reject_reason_spiega_lo_scarto():
    from radar.main import reject_reason
    from radar.models import Listing

    ok = Listing(source="t", url="https://t.it/1",
                 title="Rolex GMT-Master II 126710BLRO Pepsi",
                 raw_text="anno 2025 unworn jubilee", price_eur=31000.0)
    extract.enrich(ok, {})
    assert reject_reason(ok, W) is None

    venduto = Listing(source="t", url="https://t.it/2",
                      title="Rolex GMT-Master II 126710BLRO Pepsi",
                      raw_text="VENDUTO anno 2025", price_eur=31000.0)
    extract.enrich(venduto, {})
    assert "venduto" in reject_reason(venduto, W)

    caro = Listing(source="t", url="https://t.it/3",
                   title="Rolex GMT-Master II 126710BLRO",
                   raw_text="anno 2025", price_eur=250000.0)
    extract.enrich(caro, {})
    assert "prezzo fuori range" in reject_reason(caro, W)

    altro = Listing(source="t", url="https://t.it/4",
                    title="Rolex Datejust 126234 Jubilee",
                    raw_text="scheda con 126710BLRO in fondo", price_eur=8000.0)
    extract.enrich(altro, {})
    assert reject_reason(altro, W) is not None


def test_duplicato_vince_la_versione_piu_ricca():
    """Conte restituisce lo stesso annuncio da categoria e da ricerca."""
    from radar.main import filter_relevant
    from radar.models import Listing

    povero = Listing(source="c", url="https://c.it/orologi/pepsi/",
                     title="Rolex Gmt-Master II 126710BLRO Leggi di piu",
                     raw_text="Rolex Gmt-Master II 126710BLRO")
    ricco = Listing(source="c", url="https://c.it/orologi/pepsi/",
                    title="Rolex GMT-Master II Pepsi Jubilee 126710BLRO",
                    raw_text="Garanzia italiana anno 2025 unworn full set 20.499 €")
    for l in (povero, ricco):
        extract.enrich(l, {})

    for ordine in ((povero, ricco), (ricco, povero)):
        out = filter_relevant(list(ordine), W)
        assert len(out) == 1
        assert out[0].price_eur == 20499.0, "ha vinto la versione senza prezzo"
        assert out[0].warranty_region == "IT"


# =============================================================================
# PIÙ OROLOGI
# =============================================================================

MULTI = {
    "watches": [
        {"id": "pepsi", "brand": "Rolex", "model": "GMT-Master II",
         "nickname": "Pepsi", "references": ["126710BLRO"],
         "model_keywords": ["GMT-Master", "GMT", "Pepsi"],
         "exclude_keywords": ["BLNR", "Batman"],
         "fair_value": {"seed_price_eur": 26900}},
        {"id": "daytona", "brand": "Rolex", "model": "Daytona",
         "references": ["116500LN"],
         "model_keywords": ["Daytona"],
         "fair_value": {"seed_price_eur": 32000},
         "notifications": {"min_score": 90}},
    ],
    "fair_value": {"min_samples": 10, "lookback_days": 60,
                   "multipliers": {"year": {"_default": 1.0}}},
    "notifications": {"min_score": 78, "price_drop_pct": 2.0},
    "scoring": {"weights": {"price_vs_fair": 40}},
    "sources": [],
}


def _multi():
    from radar.config import Config
    return Config(MULTI)


def test_due_orologi_configurati():
    c = _multi()
    assert [w.id for w in c.watches] == ["pepsi", "daytona"]
    assert c.references == ["126710BLRO", "116500LN"]


def test_ogni_orologio_ha_il_suo_prezzo_di_partenza():
    pepsi, daytona = _multi().watches
    assert pepsi.get("fair_value.seed_price_eur") == 26900
    assert daytona.get("fair_value.seed_price_eur") == 32000


def test_le_impostazioni_globali_vengono_ereditate():
    pepsi, daytona = _multi().watches
    # nessuno dei due definisce lookback_days: lo prendono dal globale
    assert pepsi.get("fair_value.lookback_days") == 60
    assert daytona.get("fair_value.lookback_days") == 60
    # ma il Daytona ridefinisce la soglia di notifica
    assert pepsi.get("notifications.min_score") == 78
    assert daytona.get("notifications.min_score") == 90
    # e la regola sul calo di prezzo resta comune
    assert daytona.get("notifications.price_drop_pct") == 2.0


def test_gli_orologi_non_si_contaminano():
    """Un Daytona non deve mai passare il filtro del Pepsi, e viceversa."""
    from radar.main import reject_reason
    from radar.models import Listing
    pepsi, daytona = _multi().watches

    p = Listing(source="t", url="https://t.it/1",
                title="Rolex GMT-Master II 126710BLRO Pepsi",
                raw_text="anno 2025", price_eur=25000.0)
    d = Listing(source="t", url="https://t.it/2",
                title="Rolex Daytona 116500LN Panda",
                raw_text="anno 2023", price_eur=33000.0)
    for l in (p, d):
        extract.enrich(l, {})

    assert reject_reason(p, pepsi) is None
    assert reject_reason(d, pepsi) is not None
    assert reject_reason(d, daytona) is None
    assert reject_reason(p, daytona) is not None


def test_url_espansi_per_ogni_orologio():
    from radar.main import expand_urls
    pepsi, daytona = _multi().watches
    src = {"name": "dealer",
           "start_urls": ["https://d.it/?s={ref6}", "https://d.it/tutti-rolex"]}

    assert expand_urls(src, pepsi)["start_urls"] == [
        "https://d.it/?s=126710", "https://d.it/tutti-rolex"]
    assert expand_urls(src, daytona)["start_urls"] == [
        "https://d.it/?s=116500", "https://d.it/tutti-rolex"]


def test_url_specifici_del_modello():
    from radar.main import expand_urls
    c = _multi()
    w = c.watches[0]
    w.watch["extra_urls"] = {"dealer": ["https://d.it/gmt-master/"]}
    urls = expand_urls({"name": "dealer", "start_urls": ["https://d.it/?s={ref6}"]}, w)
    assert urls["start_urls"] == ["https://d.it/?s=126710", "https://d.it/gmt-master/"]
    # su un'altra fonte quegli URL non compaiono
    altra = expand_urls({"name": "altro", "start_urls": ["https://a.it/?s={ref6}"]}, w)
    assert altra["start_urls"] == ["https://a.it/?s=126710"]


def test_database_separa_gli_orologi(tmp_path):
    from radar.db import Database
    from radar.models import Listing
    db = Database(tmp_path / "m.db")

    p = Listing(source="t", url="https://t.it/p", reference="126710BLRO",
                price_eur=25000.0, score=80)
    d = Listing(source="t", url="https://t.it/d", reference="116500LN",
                price_eur=33000.0, score=70)
    db.upsert(p, "pepsi")
    db.upsert(d, "daytona")

    assert len(db.active_listings("pepsi")) == 1
    assert len(db.active_listings("daytona")) == 1
    assert len(db.active_listings()) == 2
    assert db.active_listings("pepsi")[0]["reference"] == "126710BLRO"

    db.save_market_snapshot("126710BLRO", 5, 26000, 24000, 26900, "pepsi")
    db.save_market_snapshot("116500LN", 4, 33000, 31000, 32000, "daytona")
    assert len(db.market_series("pepsi")) == 1
    assert db.market_series("daytona")[0]["index_value"] == 32000
    db.close()


def test_dashboard_con_due_sezioni(tmp_path):
    from radar.db import Database
    from radar.models import Listing
    db = Database(tmp_path / "d.db")
    db.upsert(Listing(source="t", url="https://t.it/p", reference="126710BLRO",
                      price_eur=25000.0, score=80), "pepsi")
    db.upsert(Listing(source="t", url="https://t.it/d", reference="116500LN",
                      price_eur=33000.0, score=70), "daytona")

    out = dashboard.build(db, [
        {"watch_id": "pepsi", "label": "Rolex GMT-Master II “Pepsi”",
         "index": 26900, "samples": 12, "data_driven": True, "median_raw": 26000},
        {"watch_id": "daytona", "label": "Rolex Daytona",
         "index": 32000, "samples": 3, "data_driven": False, "median_raw": None},
    ], tmp_path / "index.html")
    html = out.read_text(encoding="utf-8")

    assert "2 modelli monitorati" in html
    assert "Pepsi" in html and "Daytona" in html
    assert "25.000" in html and "33.000" in html
    # il Daytona ha pochi campioni: deve comparire l'avviso
    assert "Stima di partenza" in html
    db.close()


def test_retrocompatibilita_configurazione_singola():
    """Le configurazioni con `watch:` invece di `watches:` devono funzionare."""
    from radar.config import Config
    c = Config({"watch": {"id": "solo", "references": ["126710BLRO"]}})
    assert len(c.watches) == 1
    assert c.watches[0].references == ["126710BLRO"]


def test_la_cache_evita_di_riscaricare_la_stessa_pagina():
    """Con N orologi la stessa pagina veniva chiesta N volte."""
    from radar.fetch import Fetcher

    f = Fetcher({"delay_between_requests": 0})
    chiamate = []

    def finto(url):
        chiamate.append(url)
        return "<html>ok</html>", "ok"

    f._get_uncached = finto
    for _ in range(9):
        assert f.get("https://d.it/catalogo")[0] == "<html>ok</html>"
    f.get("https://d.it/altra")

    assert chiamate == ["https://d.it/catalogo", "https://d.it/altra"]
    assert f.misses == 2 and f.hits == 8


# =============================================================================
# ROTAZIONE DEI GRUPPI
# =============================================================================

ROT = {
    "rotation": {"enabled": True, "groups": ["uno-e-tre", "due-e-quattro"],
                 "aliases": {"a": "uno-e-tre", "b": "due-e-quattro"}},
    "watches": [
        {"id": "sempre", "group": "always", "references": ["126710BLRO"]},
        {"id": "uno", "group": "uno-e-tre", "references": ["4520V/210A-B128"]},
        {"id": "due", "group": "due-e-quattro", "references": ["A384"]},
        {"id": "senza-gruppo", "references": ["CAW211P"]},
    ],
}


class _Args:
    group = None
    all_watches = False


def _rot():
    from radar.config import Config
    return Config(ROT)


def test_la_rotazione_alterna_i_gruppi():
    from radar.main import select_watches
    a = _Args(); a.group = "uno-e-tre"
    b = _Args(); b.group = "b"
    ids_a = [w.id for w in select_watches(_rot(), a)[0]]
    ids_b = [w.id for w in select_watches(_rot(), b)[0]]
    assert ids_a == ["sempre", "uno", "senza-gruppo"]
    assert ids_b == ["sempre", "due", "senza-gruppo"]


def test_always_e_senza_gruppo_ci_sono_sempre():
    from radar.main import select_watches
    for g in ("a", "b"):
        args = _Args(); args.group = g
        ids = [w.id for w in select_watches(_rot(), args)[0]]
        assert "sempre" in ids, "un orologio 'always' deve essere in ogni giro"
        assert "senza-gruppo" in ids, "senza gruppo = in ogni giro"


def test_all_watches_ignora_la_rotazione():
    from radar.main import select_watches
    args = _Args(); args.all_watches = True
    picked, nome = select_watches(_rot(), args)
    assert len(picked) == 4 and nome == "tutti"


def test_rotazione_spenta_controlla_tutto():
    from radar.config import Config
    from radar.main import select_watches
    spenta = {**ROT, "rotation": {"enabled": False, "groups": ["a", "b"]}}
    picked, nome = select_watches(Config(spenta), _Args())
    assert len(picked) == 4 and nome == "tutti"


def test_il_gruppo_dipende_dalla_fascia_oraria(monkeypatch):
    """I giri delle 8/12/16/20 devono alternarsi da soli."""
    import radar.main as m
    from datetime import datetime, timezone

    visti = []
    for ora in (6, 10, 14, 18):
        finto = datetime(2026, 8, 15, ora, 0, tzinfo=timezone.utc)

        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return finto

        monkeypatch.setattr(m, "datetime", FakeDT)
        visti.append(select_watches_group(m, _rot()))
    assert visti == ["due-e-quattro", "uno-e-tre", "due-e-quattro", "uno-e-tre"], visti


def select_watches_group(m, cfg):
    return m.select_watches(cfg, _Args())[1]


def test_un_gruppo_saltato_non_chiude_gli_annunci_degli_altri(tmp_path):
    """Il bug che la rotazione avrebbe fatto emergere."""
    from radar.db import Database
    from radar.models import Listing
    db = Database(tmp_path / "r.db")

    a = Listing(source="dealer", url="https://d.it/a", price_eur=30000.0)
    b = Listing(source="dealer", url="https://d.it/b", price_eur=7000.0)
    db.upsert(a, "vc-overseas")
    db.upsert(b, "el-primero-a384")

    # giro del gruppo A: vede solo il Vacheron, e non lo trova più
    closed = db.mark_inactive_except([], ["dealer"], "vc-overseas")

    assert closed == 1
    assert [l["watch_id"] for l in db.active_listings()] == ["el-primero-a384"], \
        "il giro di un orologio ha chiuso gli annunci di un altro"
    db.close()


def test_la_dashboard_mostra_le_foto(tmp_path):
    """Foto dal config, altrimenti dagli annunci, altrimenti segnaposto."""
    from radar.db import Database
    from radar.models import Listing
    db = Database(tmp_path / "f.db")

    db.upsert(Listing(source="t", url="https://t.it/a", price_eur=9000.0,
                      image="https://cdn.it/annuncio.jpg"), "con-annuncio")

    out = dashboard.build(db, [
        {"watch_id": "da-config", "label": "Omega Speedmaster",
         "photo": "https://cdn.it/scelta-da-me.jpg", "index": 6200},
        {"watch_id": "con-annuncio", "label": "Zenith A384", "index": 7000},
        {"watch_id": "vuoto", "label": "Vacheron Constantin Overseas", "index": 30000},
    ], tmp_path / "i.html")
    html = out.read_text(encoding="utf-8")

    assert "scelta-da-me.jpg" in html, "la foto del config ha la precedenza"
    assert "annuncio.jpg" in html, "senza config si usa la foto di un annuncio"
    assert ">VC<" in html, "senza nessuna foto, le iniziali del modello"
    db.close()


def test_le_sezioni_senza_annunci_restano_chiuse(tmp_path):
    """Con otto orologi, aprire tutto rende la pagina illeggibile."""
    from radar.db import Database
    from radar.models import Listing
    db = Database(tmp_path / "c.db")
    db.upsert(Listing(source="t", url="https://t.it/x", price_eur=5000.0), "pieno")

    out = dashboard.build(db, [
        {"watch_id": "pieno", "label": "TAG Heuer Monaco", "index": 5200},
        {"watch_id": "vuoto", "label": "Zenith Elite", "index": 10000},
    ], tmp_path / "c.html")
    html = out.read_text(encoding="utf-8")

    assert '<details class="watch" id="pieno" open>' in html
    assert '<details class="watch" id="vuoto">' in html
    db.close()


def test_ridefinire_una_chiave_non_cancella_la_sezione():
    """Il bug: un orologio che ridefiniva target_years perdeva tutte le altre
    preferenze globali — geografia compresa — senza che nulla lo segnalasse."""
    from radar.config import Config
    c = Config({
        "preferences": {
            "target_years": [2025],
            "seller_locations": {"preferred": ["IT", "SM"], "neutral": ["EU"]},
            "condition_rank": ["unworn", "new", "mint"],
        },
        "scoring": {"weights": {"price_vs_fair": 40, "seller_location": 10}},
        "watches": [{
            "id": "x", "references": ["A"],
            "preferences": {"target_years": [2019, 2020]},
            "scoring": {"weights": {"price_vs_fair": 50}},
        }],
    })
    w = c.watches[0]
    prefs = w.get("preferences")

    assert prefs["target_years"] == [2019, 2020], "la chiave ridefinita vince"
    assert prefs["seller_locations"]["preferred"] == ["IT", "SM"], \
        "le altre chiavi della sezione vanno ereditate"
    assert prefs["condition_rank"] == ["unworn", "new", "mint"]

    pesi = w.get("scoring.weights")
    assert pesi["price_vs_fair"] == 50 and pesi["seller_location"] == 10


def test_la_dashboard_mette_l_italia_in_cima(tmp_path):
    """Ordine per piano geografico, non per punteggio: prima quello che
    compreresti davvero, poi il resto — che resta visibile come riferimento."""
    from radar.db import Database
    from radar.models import Listing
    db = Database(tmp_path / "geo.db")

    for paese, score in (("JP", 95), ("EU", 90), ("IT", 60), ("SM", 55)):
        db.upsert(Listing(source="c24", url=f"https://c/{paese}", price_eur=6000.0,
                          score=score, seller_country=paese), "w")

    out = dashboard.build(db, [{"watch_id": "w", "label": "Omega Speedmaster",
                                "index": 6200, "home": ["IT", "SM"],
                                "nearby": ["EU", "CH"]}], tmp_path / "g.html")
    h = out.read_text(encoding="utf-8")

    import re
    ordine = re.findall(r'<tr class="(\w+)"', h)
    assert ordine == ["home", "home", "nearby", "reference"], ordine
    # il sommario conta l'Italia, non il totale
    assert "2 in Italia" in h
    db.close()


def test_la_dashboard_mostra_anche_gli_orologi_non_controllati(tmp_path):
    """Con la rotazione si controlla mezzo gruppo per volta: se la pagina
    mostrasse solo quelli, l'altra meta' sparirebbe a ogni giro — insieme
    agli annunci che aveva gia' trovato."""
    from radar.db import Database
    from radar.models import Listing
    db = Database(tmp_path / "rot.db")

    db.upsert(Listing(source="pluswatch", url="https://p/vc", price_eur=35000.0,
                      score=76, seller_country="IT"), "vc-overseas")
    db.upsert(Listing(source="pluswatch", url="https://p/sp", price_eur=7000.0,
                      score=58, seller_country="IT"), "speedmaster")

    # giro del gruppo B: il Vacheron non e' stato controllato, ma va mostrato
    out = dashboard.build(db, [
        {"watch_id": "speedmaster", "label": "Omega Speedmaster", "index": 7300},
        {"watch_id": "vc-overseas", "label": "Vacheron Overseas",
         "index": 35000, "stale": True},
    ], tmp_path / "r.html")
    h = out.read_text(encoding="utf-8")

    assert "Vacheron Overseas" in h
    assert "35.000" in h, "l'annuncio trovato in un giro precedente deve restare"
    assert "non aggiornato in questo giro" in h
    db.close()


def test_una_fonte_vuota_non_cancella_i_ritrovamenti(tmp_path):
    """Il caso reale: un Vacheron da 35.000 e' stato marcato "non piu' online"
    perche' la ricerca di PlusWatch quel giorno tornava vuota. Una query che
    non trova nulla non prova che il negozio abbia svuotato la vetrina."""
    from radar.db import Database
    from radar.models import Listing
    db = Database(tmp_path / "v.db")
    db.upsert(Listing(source="pluswatch", url="https://p/vc", price_eur=35000.0),
              "vc-overseas")

    # la fonte risponde ma non produce annunci: non entra fra le "produttive"
    chiusi = db.mark_inactive_except([], [], "vc-overseas")
    assert chiusi == 0
    assert len(db.active_listings("vc-overseas")) == 1

    # se invece la fonte produce altri annunci, allora il vecchio e' sparito
    db.upsert(Listing(source="pluswatch", url="https://p/altro", price_eur=30000.0),
              "vc-overseas")
    altro = Listing(source="pluswatch", url="https://p/altro")
    chiusi = db.mark_inactive_except([altro.key], ["pluswatch"], "vc-overseas")
    assert chiusi == 1
    db.close()


def test_gli_alias_dei_gruppi_continuano_a_funzionare():
    """Rinominare un gruppo non deve far girare a vuoto un lancio vecchio."""
    from radar.main import select_watches
    args = _Args(); args.group = "a"
    picked, nome = select_watches(_rot(), args)
    assert nome == "uno-e-tre"
    assert [w.id for w in picked] == ["sempre", "uno", "senza-gruppo"]


def test_un_gruppo_inesistente_controlla_tutto_invece_di_niente():
    """Il fallimento silenzioso peggiore sarebbe un giro che non guarda nulla."""
    from radar.main import select_watches
    args = _Args(); args.group = "zzz"
    picked, nome = select_watches(_rot(), args)
    assert nome == "tutti"
    assert len(picked) == 4


def test_si_puo_chiedere_un_orologio_solo_per_nome():
    """Quando ne aggiungi uno vuoi provarlo subito, non al suo turno."""
    from radar.main import select_watches
    args = _Args(); args.group = "due"
    picked, nome = select_watches(_rot(), args)
    assert [w.id for w in picked] == ["due"]
    assert nome == "due"


def test_i_falsi_annunci_gia_salvati_vengono_tolti(tmp_path):
    """Correggere il lettore non basta: il finto affare era gia' in dashboard."""
    from radar.db import Database
    from radar.models import Listing
    db = Database(tmp_path / "p.db")
    db.upsert(Listing(source="chrono24", price_eur=5612.0,
                      url="https://www.chrono24.com/user/searchtasks.htm?eeid=XX"), "speedmaster")
    db.upsert(Listing(source="chrono24", price_eur=7600.0,
                      url="https://www.chrono24.it/omega/s--id48015818.htm"), "speedmaster")
    db.conn.commit(); db.close()

    db = Database(tmp_path / "p.db")
    urls = [r["url"] for r in db.conn.execute("SELECT url FROM listings")]
    assert len(urls) == 1 and "id48015818" in urls[0], urls


def test_la_pulizia_non_tocca_gli_annunci_veri(tmp_path):
    """I link Chrono24 contengono 'feat-SavedSearch' nel tracciamento: una
    regola che guardasse anche la query cancellerebbe annunci veri. Successo
    davvero, 25 righe in una volta."""
    from radar.db import Database
    from radar.models import Listing
    vero = ("https://www.chrono24.it/rolex/gmt-master-ii--id47857408.htm"
            "?eeid=XX&ikcampaign=feat-SavedSearch&ikterm=AdImageLink")
    db = Database(tmp_path / "q.db")
    db.upsert(Listing(source="chrono24", price_eur=22405.0, url=vero), "pepsi")
    db.conn.commit(); db.close()

    db = Database(tmp_path / "q.db")
    assert db.conn.execute("SELECT count(*) FROM listings").fetchone()[0] == 1


# --- riconoscere gli orologi come li scrivono i venditori ---------------------

_TITOLI_VERI = {
    "monaco-gulf":        ("TAG Heuer Monaco Gulf Special Edition CAW211P", True),
    "speedmaster":        ("Omega Speedmaster Moonwatch Professional 310.30.42.50.01.002", True),
    "bb-chrono-flamingo": ("Tudor Black Bay Chrono 79360N Flamingo Blue", True),
    "zenith-ultrathin":   ("Zenith Elite Classic Automatic Ultra Thin", True),
    "vc-222":             ("Vacheron Constantin Historiques 222 acciaio 4200H", True),
    "vc-overseas":        ("Vacheron Constantin Overseas Automatic 41mm 4520V", True),
    "royal-oak-15450":    ("Audemars Piguet Royal Oak 37mm 15450ST blu", True),
    "el-primero-a384":    ("Zenith El Primero A384 Revival 37mm 2023", True),
}


def _config_vera():
    from radar.config import Config
    return Config.load("config.yaml")


def test_ogni_orologio_si_riconosce_da_come_lo_scrivono_davvero():
    """Il 20/08 sette orologi su otto erano invisibili: passavano solo i
    titoli con la referenza completa, che quasi nessuno scrive."""
    from radar.main import reject_reason
    from radar.models import Listing
    for w in _config_vera().watches:
        if w.id not in _TITOLI_VERI:
            continue
        titolo, atteso = _TITOLI_VERI[w.id]
        motivo = reject_reason(Listing(source="x", url="https://a/" + w.id,
                                       title=titolo), w)
        assert (motivo is None) is atteso, f"{w.id}: {titolo} -> {motivo}"


def test_lo_speedmaster_zaffiro_resta_fuori():
    """Il .04.001 e' la cassa zaffiro: altro orologio, altro prezzo. Era gia'
    stato escluso una volta, le radici di referenza rischiavano di riaprirlo."""
    from radar.main import reject_reason
    from radar.models import Listing
    w = next(x for x in _config_vera().watches if x.id == "speedmaster")
    motivo = reject_reason(Listing(
        source="x", url="https://a/z",
        title="Omega Speedmaster Professional Moonwatch 310.30.42.50.04.001"), w)
    assert motivo is not None, "lo Speedmaster zaffiro non deve passare"


def test_le_radici_non_aprono_ad_altri_modelli():
    from radar.main import reject_reason
    from radar.models import Listing
    fuori = {
        "monaco-gulf": "TAG Heuer Carrera CBN2A1B.BA0643",
        "bb-chrono-flamingo": "Tudor Black Bay 58 79030N",
        "vc-overseas": "Vacheron Constantin Historiques 222 4200H",
        "royal-oak-15450": "Audemars Piguet Royal Oak 41mm 15500ST.OO.1220ST.01",
    }
    for w in _config_vera().watches:
        if w.id in fuori:
            l = Listing(source="x", url="https://a/x", title=fuori[w.id])
            assert reject_reason(l, w) is not None, f"{w.id}: {fuori[w.id]}"


def test_lo_zenith_accetta_i_prezzi_veri_osservati():
    """I tre annunci italiani del 26/08: 3.490, 3.500, 4.300 euro. La vecchia
    soglia minima di 4.000 ne scartava due su tre, e la stima di 12.500 li
    avrebbe fatti sembrare tutti affari del settanta per cento."""
    from radar.main import reject_reason
    from radar.models import Listing
    w = next(x for x in _config_vera().watches if x.id == "zenith-ultrathin")
    for prezzo in (3490.0, 3500.0, 4300.0):
        l = Listing(source="x", url=f"https://a/{prezzo}",
                    title="Zenith Elite Classic Automatic Ultra Thin",
                    price_eur=prezzo)
        assert reject_reason(l, w) is None, prezzo
    seed = float(w.get("fair_value.seed_price_eur"))
    assert 3000 <= seed <= 4500, f"stima fuori dai prezzi osservati: {seed}"


def test_la_famiglia_elite_entra_tutta_ma_il_resto_di_zenith_no():
    """Hai chiesto di prenderne qualcuno in piu' piuttosto che in meno."""
    from radar.main import reject_reason
    from radar.models import Listing
    w = next(x for x in _config_vera().watches if x.id == "zenith-ultrathin")
    dentro = ["Zenith Elite Classic Automatic Ultra Thin",
              "Zenith Elite 6150 Ultra Thin 42mm acciaio",
              "Zenith Elite Classic 33mm automatico"]
    fuori = ["Zenith Chronomaster Open Elite calibro",
             "Zenith Defy Skyline 41mm",
             "Zenith El Primero A384 Revival",
             "Omega De Ville Prestige Ultra Thin"]
    for t in dentro:
        assert reject_reason(Listing(source="x", url="https://a/"+t[:9],
                                     title=t, price_eur=3600.0), w) is None, t
    for t in fuori:
        assert reject_reason(Listing(source="x", url="https://a/"+t[:9],
                                     title=t, price_eur=3600.0), w) is not None, t


# --- guardie strutturali sul config ------------------------------------------
#
# Non verificano che le stime siano giuste — nessun test puo' farlo — ma che
# non siano messe in modo da nascondere annunci. E' la differenza fra sbagliare
# un numero e non vedere un'occasione.

def test_la_soglia_minima_non_taglia_via_gli_affari():
    """Un annuncio molto sotto mercato deve poter entrare.

    Il caso vero: sullo Zenith la soglia era 4.000 euro e i prezzi reali
    stavano fra 3.490 e 4.300. Due annunci su tre venivano scartati come
    'prezzo implausibile' senza che nessuno se ne accorgesse.
    """
    for w in _config_vera().watches:
        seed = float(w.get("fair_value.seed_price_eur"))
        minimo = float(w.get("hard_filters", {}).get("absolute_min_price_eur", 0))
        assert minimo <= seed * 0.55, (
            f"{w.id}: soglia minima {minimo:,.0f} troppo vicina alla stima "
            f"{seed:,.0f} — un affare vero verrebbe scartato")


def test_la_soglia_massima_lascia_spazio_al_mercato():
    """Se il mercato sale, il tetto non deve azzerare la fonte."""
    for w in _config_vera().watches:
        seed = float(w.get("fair_value.seed_price_eur"))
        massimo = float(w.get("hard_filters", {}).get("absolute_max_price_eur", 0))
        assert massimo >= seed * 1.8, f"{w.id}: tetto {massimo:,.0f} troppo basso"


def test_ogni_orologio_e_identificabile():
    """Senza referenze e senza modo 'name' un orologio e' invisibile e basta.
    Era la situazione dello Zenith, con una referenza che non esisteva."""
    for w in _config_vera().watches:
        if w.identify_by == "name":
            assert w.must_include, f"{w.id}: modo 'name' senza parole da cercare"
        else:
            assert w.references, f"{w.id}: nessuna referenza e nessun nome"
