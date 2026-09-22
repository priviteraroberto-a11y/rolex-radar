"""Il menu di GitHub deve elencare gli orologi che ci sono davvero.

Su GitHub la tendina di "Run workflow" e' un elenco fisso dentro il YAML: non
si puo' riempire al volo. Quindi e' per forza una copia degli `id` del config,
e le copie divergono.

E' gia' costato: dopo il rinomino dei gruppi di rotazione il workflow aveva un
`case "a|b")` rimasto indietro, e la scelta veniva buttata via senza un
errore — il giro partiva sulla rotazione automatica e sembrava tutto normale.

Adesso la copia la rifa' `pubblica.sh` a ogni pubblicazione. Questo test
controlla che sia stata rifatta.
"""
import subprocess
import sys
from pathlib import Path

import yaml

RADICE = Path(__file__).resolve().parent.parent
WORKFLOW = RADICE / ".github" / "workflows" / "check.yml"


def _workflow() -> dict:
    d = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # `on:` in YAML viene letto come True, che e' una vecchia trappola del
    # formato: la chiave puo' arrivare come stringa o come booleano.
    return d[next(k for k in d if k in ("on", True))]


def _ids() -> list[str]:
    dati = yaml.safe_load((RADICE / "config.yaml").read_text(encoding="utf-8"))
    return [str(w["id"]) for w in dati["watches"]]


def _opzioni() -> list[str]:
    return _workflow()["workflow_dispatch"]["inputs"]["orologio"]["options"]


def test_il_menu_elenca_tutti_gli_orologi():
    opzioni = _opzioni()
    assert opzioni[0] == "tutti", "la prima voce deve essere il giro completo"
    assert [o.rsplit(" \u2014 ", 1)[-1] for o in opzioni[1:]] == _ids(), (
        "menu disallineato dal config — lancia `python tools/aggiorna_menu.py`")


def test_nessuna_voce_e_uguale_a_un_altra():
    """Due voci identiche nella tendina sono peggio delle sigle.

    Senza il soprannome succedeva: "Tudor Black Bay Chrono" compariva due
    volte (Flamingo e Panda) e i due Speedmaster erano indistinguibili.
    """
    nomi = [o.rsplit(" — ", 1)[0] for o in _opzioni()[1:]]
    doppi = {n for n in nomi if nomi.count(n) > 1}
    assert not doppi, f"voci ambigue: {doppi}"


def test_il_menu_mostra_i_nomi_non_le_sigle():
    """Roberto non deve ricordarsi che il Base Logo si chiama
    `panerai-base-logo`: la tendina porta il nome, l'id resta in coda."""
    for o in _opzioni()[1:]:
        assert " \u2014 " in o, f"voce senza nome leggibile: {o}"
        nome, id_ = o.rsplit(" \u2014 ", 1)
        assert len(nome) > len(id_) / 2 and " " in nome, o


def test_la_voce_scelta_torna_a_essere_un_id():
    """Quello che GitHub passa al programma non e' un id nudo."""
    import yaml as _y
    from radar.config import Config
    from radar.main import select_watches

    class _A:
        solo = None
        group = None
        all_watches = False

    cfg = Config(_y.safe_load((RADICE / "config.yaml").read_text(encoding="utf-8")))
    for voce in _opzioni()[1:]:
        a = _A(); a.solo = voce
        scelti, nome = select_watches(cfg, a)
        assert len(scelti) == 1, f"{voce} non ha selezionato un orologio solo"
        assert scelti[0].id == voce.rsplit(" \u2014 ", 1)[-1]

    a = _A(); a.solo = "tutti"
    assert len(select_watches(cfg, a)[0]) == len(cfg.watches)


def test_lo_script_e_idempotente():
    """Rilanciarlo due volte non deve cambiare niente la seconda.

    Se non lo fosse, `pubblica.sh` produrrebbe una modifica a ogni giro anche
    quando non e' cambiato nulla, e il repository si riempirebbe di commit che
    non dicono niente.
    """
    prima = WORKFLOW.read_text(encoding="utf-8")
    for _ in range(2):
        r = subprocess.run([sys.executable, str(RADICE / "tools" / "aggiorna_menu.py")],
                           capture_output=True, text=True, cwd=RADICE)
        assert r.returncode == 0, r.stderr
    assert WORKFLOW.read_text(encoding="utf-8") == prima


def test_la_verifica_accorge_del_disallineamento(tmp_path):
    """`--verifica` deve fallire quando il menu non corrisponde."""
    originale = WORKFLOW.read_text(encoding="utf-8")
    rotto = originale.replace("          - tutti\n",
                              "          - tutti\n          - orologio-inventato\n", 1)
    try:
        WORKFLOW.write_text(rotto, encoding="utf-8")
        r = subprocess.run([sys.executable, str(RADICE / "tools" / "aggiorna_menu.py"),
                            "--verifica"], capture_output=True, text=True, cwd=RADICE)
        assert r.returncode == 1, "il disallineamento e' passato inosservato"
    finally:
        WORKFLOW.write_text(originale, encoding="utf-8")


def test_il_workflow_resta_leggibile():
    """Uno script che riscrive un YAML puo' romperlo: qui si controlla."""
    w = _workflow()
    assert "schedule" in w and "workflow_dispatch" in w
    inputs = w["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"dry_run", "force", "orologio"}
    assert inputs["orologio"]["default"] == "tutti"
