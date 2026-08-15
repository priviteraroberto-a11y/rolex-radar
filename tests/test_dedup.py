"""Test della deduplica: nessuno vuole 120 notifiche per lo stesso annuncio."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.config import Config             # noqa: E402
from radar.db import Database               # noqa: E402
from radar.fairvalue import FairValueEngine # noqa: E402
from radar.models import Listing            # noqa: E402
from radar.notify.decide import decide_notifications  # noqa: E402
from radar.scorer import Scorer             # noqa: E402

# Un orologio di prova, per non dipendere dalla configurazione di produzione.
_REALE = Config.load(Path(__file__).resolve().parent.parent / "config.yaml")
CFG = Config({**_REALE.raw, "watches": [{
    "id": "pepsi-test", "brand": "Rolex", "model": "GMT-Master II",
    "references": ["126710BLRO"],
    "model_keywords": ["GMT-Master", "GMT", "Pepsi"],
    "exclude_keywords": ["BLNR", "Batman"],
}]}).watches[0]
ENGINE = FairValueEngine(CFG, [])
SCORER = Scorer(CFG)


def mk(price, url="https://t.it/a", score_year=2025) -> Listing:
    l = Listing(source="t", url=url, reference="126710BLRO", year=score_year,
                condition="unworn", bracelet="jubilee", full_set=True,
                warranty_region="IT", never_polished=True, seller_trust=4,
                price_eur=price)
    return SCORER.score(ENGINE.evaluate(l))


def test_url_normalizzato_per_la_chiave():
    a = Listing(source="t", url="https://x.it/annuncio/123?utm_source=mail")
    b = Listing(source="t", url="https://x.it/annuncio/123/")
    assert a.key == b.key


def test_stesso_annuncio_non_rinotificato(tmp_path):
    db = Database(tmp_path / "t.db")
    l = mk(ENGINE.index * 0.98)

    first = db.upsert(l)
    assert first["is_new"]
    d1 = decide_notifications([(l, first)], CFG, ENGINE.is_underpriced, force=True)
    assert len(d1) == 1

    second = db.upsert(l)
    assert not second["is_new"]
    d2 = decide_notifications([(l, second)], CFG, ENGINE.is_underpriced, force=True)
    assert d2 == []
    db.close()


def test_calo_di_prezzo_rinotifica(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert(mk(32000))
    ribassato = mk(30500)
    change = db.upsert(ribassato)
    d = decide_notifications([(ribassato, change)], CFG, ENGINE.is_underpriced, force=True)
    assert len(d) == 1 and d[0].reason in ("price_drop", "underpriced")
    db.close()


def test_ritocco_minimo_non_rinotifica(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert(mk(ENGINE.index * 1.05))
    quasi = mk(ENGINE.index * 1.05 - 50)          # −50 €: sotto entrambe le soglie
    change = db.upsert(quasi)
    d = decide_notifications([(quasi, change)], CFG, ENGINE.is_underpriced, force=True)
    assert d == []
    db.close()


def test_sotto_mercato_passa_anche_nelle_ore_di_silenzio(tmp_path):
    db = Database(tmp_path / "t.db")
    occasione = mk(ENGINE.index * 0.80)
    change = db.upsert(occasione)
    # force=False → applica le ore di silenzio; priority 100 deve passare comunque
    d = decide_notifications([(occasione, change)], CFG, ENGINE.is_underpriced, force=False)
    assert len(d) == 1 and d[0].reason == "underpriced"
    db.close()


def test_storico_prezzi_registrato(tmp_path):
    db = Database(tmp_path / "t.db")
    l = mk(32000)
    db.upsert(l)
    assert len(db.price_series(l.key)) == 1
    db.close()


def test_annunci_scaduti_chiusi_solo_per_fonti_sane(tmp_path):
    db = Database(tmp_path / "t.db")
    a = mk(32000, url="https://t.it/a")
    b = mk(31000, url="https://t.it/b")
    db.upsert(a)
    db.upsert(b)

    # la fonte "t" ha risposto e vede solo A → B va chiuso
    closed = db.mark_inactive_except([a.key], ["t"])
    assert closed == 1
    assert len(db.active_listings()) == 1

    # se la fonte NON ha risposto, non si chiude nulla
    db.upsert(b)
    closed2 = db.mark_inactive_except([], [])
    assert closed2 == 0
    db.close()


def test_tetto_notifiche_per_giro(tmp_path):
    db = Database(tmp_path / "t.db")
    batch = []
    for i in range(40):
        l = mk(ENGINE.index * 0.80, url=f"https://t.it/{i}")
        batch.append((l, db.upsert(l)))
    d = decide_notifications(batch, CFG, ENGINE.is_underpriced, force=True)
    assert len(d) <= CFG.get("notifications.max_per_run")
    db.close()


def test_il_paese_del_venditore_viene_salvato(tmp_path):
    """Serve alla dashboard e alla pastiglia: se non si salva, sparisce."""
    from radar.models import Listing
    db = Database(tmp_path / "geo.db")
    l = Listing(source="chrono24", url="https://c/1", reference="126710BLRO",
                price_eur=25000.0, seller_country="SM")
    db.upsert(l, "test")
    salvato = db.active_listings("test")[0]
    assert salvato["seller_country"] == "SM"
    db.close()
