"""Il messaggio deve dire QUALE orologio ha trovato.

Per settimane ogni notifica Telegram ha annunciato un "Rolex GMT-Master II
Pepsi": foto giusta, prezzo giusto, link giusto, nome sbagliato. Era una
scritta fissa nel codice, rimasta da quando gli orologi seguiti erano uno solo.

Non rompeva niente, e proprio per questo e' durato: nessun test falliva,
nessun log si lamentava. Si vedeva solo leggendo il messaggio.
"""
import yaml
from pathlib import Path

from radar.config import Config
from radar.models import Listing
from radar.notify.decide import NotifyDecision, decide_notifications
from radar.notify.telegram import TelegramNotifier

RADICE = Path(__file__).resolve().parent.parent


def _config():
    return Config(yaml.safe_load((RADICE / "config.yaml").read_text(encoding="utf-8")))


def _messaggio(d: NotifyDecision) -> str:
    return TelegramNotifier()._format(d)


def _annuncio(**kw):
    base = dict(source="zorzoli", url="https://zorzoliorologi.com/products/x",
                title="Vacheron Constantin Overseas", price_eur=25900.0, score=80)
    base.update(kw)
    return Listing(**base)


def test_il_messaggio_porta_il_nome_dell_orologio():
    d = NotifyDecision(_annuncio(), "underpriced", "SOTTO MERCATO", 100,
                       label="Vacheron Overseas 41 (4500V)")
    testo = _messaggio(d)
    assert "Vacheron Overseas 41 (4500V)" in testo


def test_nessun_orologio_annuncia_un_pepsi():
    """La prova che conta: per ognuno dei sedici, il nome giusto e nient'altro."""
    for w in _config().watches:
        d = NotifyDecision(_annuncio(title=w.label), "new", "NUOVO ANNUNCIO", 70,
                           label=w.label)
        testo = _messaggio(d)
        assert "Pepsi" not in testo, f"{w.id} annuncia un Pepsi"
        assert "126710BLRO" not in testo, f"{w.id} porta la referenza del Pepsi"
        assert w.label.split("(")[0].strip()[:18] in testo, w.id


def test_senza_nome_si_ripiega_sul_titolo_dell_annuncio():
    """Mai inventare: se il nome non c'e', si usa quello che dice l'annuncio."""
    d = NotifyDecision(_annuncio(title="Omega Speedmaster Snoopy"),
                       "new", "NUOVO ANNUNCIO", 70)
    assert "Omega Speedmaster Snoopy" in _messaggio(d)


def test_la_referenza_attesa_e_dichiarata_come_tale():
    """Se l'annuncio non dichiara la referenza, non gliene attribuiamo una.

    Il codice di prima ci metteva `126710BLRO` come ripiego, cioe' scriveva
    sotto la foto di un Panerai la referenza di un Rolex.
    """
    d = NotifyDecision(_annuncio(reference=None), "new", "NUOVO ANNUNCIO", 70,
                       label="Vacheron Overseas 41 (4520V)",
                       reference_attesa="4520V/210A-B128")
    testo = _messaggio(d)
    assert "4520V/210A-B128" in testo and "attesa" in testo

    vero = NotifyDecision(_annuncio(reference="4500V/110A-B128"), "new",
                          "NUOVO ANNUNCIO", 70, label="Overseas")
    assert "attesa" not in _messaggio(vero)


def test_decide_riempie_il_nome_da_solo():
    """Il nome deve arrivare senza che chi chiama se lo ricordi.

    Se restasse da passare a mano, prima o poi qualcuno lo dimenticherebbe e
    tornerebbero i messaggi senza nome.
    """
    cfg = _config()
    w = next(x for x in cfg.watches if x.id == "panerai-base-logo")
    l = _annuncio(title="Panerai Luminor Base Logo 44mm PAM01086",
                  price_eur=3200.0, score=95, seller_country="IT",
                  fair_value_eur=4414.0, delta_eur=1214.0, delta_pct=27.5)
    decisioni = decide_notifications([(l, {"is_new": True, "old_price": None,
                                           "old_score": 0})],
                                     w, lambda l, soglia: True, force=True)
    assert decisioni, "nessuna decisione prodotta"
    assert decisioni[0].label == w.label
    assert w.label.split("(")[0].strip()[:18] in _messaggio(decisioni[0])
