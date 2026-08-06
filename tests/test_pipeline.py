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

CFG = Config.load(Path(__file__).resolve().parent.parent / "config.yaml")


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
    relevant = filter_relevant(listings, CFG)
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
    engine = FairValueEngine(CFG, [])
    scorer = Scorer(CFG)

    for l in listings:
        engine.evaluate(l)
        scorer.score(l)
        db.upsert(l)

    db.save_market_snapshot("126710BLRO", engine.n, engine.median_raw,
                            engine.p25, engine.index)

    active = db.active_listings()
    assert len(active) == 2
    assert all(0 <= a["score"] <= 100 for a in active)

    out = dashboard.build(db, engine.summary(), tmp_path / "index.html")
    html = out.read_text(encoding="utf-8")
    assert "126710BLRO" in html
    assert "33.900" in html and "28.400" in html
    assert "{" not in html.split("<script>")[0].split("<style>")[0]  # niente placeholder
    db.close()


def test_dashboard_gestisce_database_vuoto(tmp_path):
    db = Database(tmp_path / "v.db")
    engine = FairValueEngine(CFG, [])
    out = dashboard.build(db, engine.summary(), tmp_path / "index.html")
    assert "ROLEX RADAR" in out.read_text(encoding="utf-8")
    db.close()
