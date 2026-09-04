"""Le rilevazioni di mercato: campioni raccolti a mano per fondare l'indice."""
import json

import pytest

from radar.config import Config
from radar.db import Database
from radar.fairvalue import FairValueEngine
from radar import rilevazione


CFG = Config({
    "fair_value": {"min_samples": 10, "lookback_days": 60,
                   "multipliers": {"year": {2025: 1.0, 2020: 0.85},
                                   "condition": {"unworn": 1.0, "good": 0.79}}},
    "watches": [
        {"id": "per-referenza", "brand": "Omega", "references": ["310.30.42.50.01.002"]},
        {"id": "per-nome", "brand": "Zenith", "identify_by": "name",
         "must_include": ["Elite"], "references": []},
    ],
})


def _scrivi(tmp_path, campioni, data="2026-09-03"):
    d = tmp_path / "rilevazioni"; d.mkdir(exist_ok=True)
    (d / f"{data}-chrono24.json").write_text(json.dumps(
        {"fonte": "chrono24", "data": data, "campioni": campioni}), encoding="utf-8")
    return d


def test_i_campioni_alimentano_l_indice(tmp_path):
    cartella = _scrivi(tmp_path, {"per-referenza": [{"p": 6000 + i * 100, "c": "DE"}
                                                    for i in range(12)]})
    db = Database(tmp_path / "a.db")
    assert rilevazione.importa(db, CFG, cartella) == 12
    fv = FairValueEngine(CFG.watches[0], db.comparables("per-referenza", 60))
    assert fv.data_driven, "dodici campioni devono bastare"
    assert 6000 <= fv.index <= 7200, fv.index


def test_anche_gli_orologi_riconosciuti_per_nome(tmp_path):
    """Prima l'indice raggruppava per referenza: uno Zenith Elite, che di
    referenze non ne ha, non poteva avere nemmeno un comparabile e restava
    per sempre appeso alla stima scritta a mano."""
    cartella = _scrivi(tmp_path, {"per-nome": [{"p": 3000 + i * 50, "c": "IT"}
                                               for i in range(12)]})
    db = Database(tmp_path / "b.db")
    rilevazione.importa(db, CFG, cartella)
    fv = FairValueEngine(CFG.watches[1], db.comparables("per-nome", 60))
    assert fv.data_driven and fv.n >= 10, (fv.n, fv.index)


def test_non_diventano_annunci_da_comprare(tmp_path):
    """Sono termini di paragone, non occasioni: non devono comparire in
    dashboard ne' generare notifiche."""
    cartella = _scrivi(tmp_path, {"per-referenza": [{"p": 6500, "c": "DE"}]})
    db = Database(tmp_path / "c.db")
    rilevazione.importa(db, CFG, cartella)
    assert db.active_listings("per-referenza") == []
    assert db.conn.execute("SELECT count(*) FROM listings WHERE active=1"
                           ).fetchone()[0] == 0


def test_reimportare_non_duplica(tmp_path):
    """Il giro automatico la rilegge quattro volte al giorno."""
    cartella = _scrivi(tmp_path, {"per-referenza": [{"p": 6500, "c": "DE"},
                                                    {"p": 6900, "c": "FR"}]})
    db = Database(tmp_path / "d.db")
    assert rilevazione.importa(db, CFG, cartella) == 2
    assert rilevazione.importa(db, CFG, cartella) == 0
    assert db.conn.execute("SELECT count(*) FROM listings").fetchone()[0] == 2


def test_la_pulizia_del_database_non_le_cancella(tmp_path):
    """Il loro indirizzo non e' un vero link: va verificato che sopravviva
    al controllo che butta via vetrine e immagini."""
    from radar.extract import e_url_di_annuncio
    cartella = _scrivi(tmp_path, {"per-referenza": [{"p": 6500, "c": "DE"}]})
    db = Database(tmp_path / "e.db")
    rilevazione.importa(db, CFG, cartella)
    url = db.conn.execute("SELECT url FROM listings").fetchone()["url"]
    assert e_url_di_annuncio(url), url
    db.close()
    db = Database(tmp_path / "e.db")          # la pulizia gira a ogni avvio
    assert db.conn.execute("SELECT count(*) FROM listings").fetchone()[0] == 1


def test_una_rilevazione_rotta_non_ferma_il_giro(tmp_path):
    d = tmp_path / "rilevazioni"; d.mkdir()
    (d / "rotta.json").write_text("{non sono json", encoding="utf-8")
    db = Database(tmp_path / "f.db")
    assert rilevazione.importa(db, CFG, d) == 0
    assert rilevazione.importa(db, CFG, tmp_path / "inesistente") == 0


def test_il_dato_ignoto_non_penalizza():
    """Nella tabella degli anni `_default` vuol dire "piu' vecchio del 2022".
    Applicarlo a chi l'anno non lo dichiara equivale a dare per scontato il
    peggio, e a far sembrare caro un annuncio che caro non e'."""
    from radar.config import multiplier
    anni = {2026: 1.06, 2025: 1.00, 2022: 0.88, "_default": 0.85}
    assert multiplier(anni, None) == 1.0
    assert multiplier(anni, 2022) == 0.88
    assert multiplier(anni, 2015) == 0.85          # dichiarato e vecchio
    assert multiplier({"true": 1.0, "false": 0.93, "_unknown": 0.97}, None) == 0.97
