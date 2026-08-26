"""Il giro completo dell'aggiunta di un orologio dal modulo web."""
import importlib.util
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location(
        "aggiungi_modello", RADICE / "tools" / "aggiungi_modello.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aggiungi_modello"] = mod
    spec.loader.exec_module(mod)
    return mod


def _richiesta(**campi) -> str:
    base = {"id": "prova", "brand": "Omega", "model": "Seamaster",
            "references": '["234.30.41.21.01.001"]', "seed": 5000,
            "min": 2250, "max": 11000}
    base.update(campi)
    return f"""Aggiungi questo orologio.

```yaml
  - id: {base['id']}
    group: mettici-quello-che-vuoi
    brand: {base['brand']}
    model: {base['model']}
    references: {base['references']}
    hard_filters:
      absolute_min_price_eur: {base['min']}
      absolute_max_price_eur: {base['max']}
    fair_value:
      seed_price_eur: {base['seed']}
```
"""


@pytest.fixture
def config(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text((RADICE / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return p


def _esegui(tool, corpo, config, tmp_path):
    b = tmp_path / "b.md"; b.write_text(corpo, encoding="utf-8")
    esito = tmp_path / "e.md"
    codice = tool.main.__wrapped__ if hasattr(tool.main, "__wrapped__") else None
    sys.argv = ["x", "--body", str(b), "--config", str(config),
                "--out-message", str(esito)]
    rc = tool.main()
    return rc, esito.read_text(encoding="utf-8")


def test_i_gruppi_restano_bilanciati(config, tmp_path):
    """Due aggiunte di fila devono finire in due turni diversi."""
    import yaml
    from collections import Counter
    tool = _tool()
    for i, nome in enumerate(("uno", "due")):
        rc, msg = _esegui(tool, _richiesta(id=f"prova-{nome}"), config, tmp_path)
        assert rc == 0, msg
    d = yaml.safe_load(config.read_text(encoding="utf-8"))
    conteggio = Counter(w.get("group") for w in d["watches"])
    assert max(conteggio.values()) - min(conteggio.values()) <= 1, conteggio


def test_il_gruppo_scritto_nella_richiesta_non_conta(config, tmp_path):
    """Decide chi scrive, non chi chiede: fra i due momenti puo' cambiare."""
    import yaml
    tool = _tool()
    _esegui(tool, _richiesta(id="prova-gruppo"), config, tmp_path)
    d = yaml.safe_load(config.read_text(encoding="utf-8"))
    w = next(x for x in d["watches"] if x["id"] == "prova-gruppo")
    gruppi = d["rotation"]["groups"]
    assert w["group"] in gruppi, w["group"]


def test_i_commenti_del_config_sopravvivono(config, tmp_path):
    """Meta' del valore di quel file sono i commenti: spiegano quali errori
    sono gia' costati caro. Una riscrittura con yaml.dump li cancellerebbe."""
    prima = config.read_text(encoding="utf-8").count("#")
    _esegui(_tool(), _richiesta(id="prova-commenti"), config, tmp_path)
    assert config.read_text(encoding="utf-8").count("#") == prima


@pytest.mark.parametrize("corpo,atteso", [
    (_richiesta(id="speedmaster"), "c'e' gia'"),
    (_richiesta(id="p1", seed=10000, min=9000, max=20000), "soglia minima"),
    (_richiesta(id="p2", seed=10000, min=1000, max=12000), "tetto"),
    (_richiesta(id="p3", references="[]"), "riconoscibile"),
    (_richiesta(id="Prova Maiuscola"), "non e' valido"),
    ("nessun blocco qui", "blocco"),
])
def test_le_richieste_sbagliate_vengono_rifiutate_con_una_spiegazione(
        corpo, atteso, config, tmp_path):
    prima = config.read_text(encoding="utf-8")
    rc, msg = _esegui(_tool(), corpo, config, tmp_path)
    assert rc == 1
    assert atteso in msg, msg
    assert config.read_text(encoding="utf-8") == prima, "il config e' stato toccato"


def test_il_config_resta_leggibile_dal_radar(config, tmp_path):
    """Un config rotto ferma tutti i giri, non solo quello nuovo."""
    from radar.config import Config
    _esegui(_tool(), _richiesta(id="prova-finale"), config, tmp_path)
    cfg = Config.load(str(config))
    w = next(x for x in cfg.watches if x.id == "prova-finale")
    assert w.references and w.get("fair_value.seed_price_eur") == 5000


def test_la_pagina_e_lo_script_scelgono_lo_stesso_turno():
    """Se divergessero, la pagina ti direbbe una cosa e il sistema ne farebbe
    un'altra — il tipo di bugia che non ti accorgi di ricevere."""
    import yaml
    from radar.config import Config
    from radar import nuovo_modello
    tool = _tool()
    grezzo = yaml.safe_load((RADICE / "config.yaml").read_text(encoding="utf-8"))
    assert (nuovo_modello.gruppo_meno_affollato(Config.load(str(RADICE / "config.yaml")))
            == tool.gruppo_meno_affollato(grezzo))


def test_il_modulo_web_si_genera(tmp_path):
    from radar.config import Config
    from radar import nuovo_modello
    import re
    cfg = Config.load(str(RADICE / "config.yaml"))
    p = nuovo_modello.build(cfg, tmp_path / "nuovo.html")
    t = p.read_text(encoding="utf-8")
    assert not re.findall(r"(?<!\{)\{[a-z_]+\}(?!\})", t), "segnaposto non sostituiti"
    assert "issues/new" in t and "nuovo-modello" in t
    for w in cfg.watches:                     # gli id esistenti, per il duplicato
        assert w.id in t
