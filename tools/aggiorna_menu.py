#!/usr/bin/env python3
"""Riscrive il menu a tendina di GitHub leggendo gli orologi dal config.

Il problema che risolve
-----------------------
Su GitHub il menu a tendina di "Run workflow" deve essere un elenco fisso
dentro il file YAML: non esiste modo di riempirlo a runtime. E un elenco
scritto a mano in un secondo posto e' una copia, e le copie divergono.

E' gia' successo: i gruppi di rotazione erano stati rinominati nel config e il
`case "a|b")` nel workflow era rimasto indietro. La scelta veniva scartata in
silenzio — nessun errore, il giro partiva semplicemente su tutto.

La soluzione non e' ricordarsi di aggiornare tutte e due: e' non avere due
elenchi. Questo script rigenera la tendina dal config, e `pubblica.sh` lo
chiama prima dei test. Se aggiungi un orologio, il menu se ne accorge da solo.

    python tools/aggiorna_menu.py            # riscrive se serve
    python tools/aggiorna_menu.py --verifica # dice solo se e' disallineato

Con `--verifica` esce con codice 1 se il menu non corrisponde: comodo se un
giorno vorrai controllarlo anche in automatico.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

RADICE = Path(__file__).resolve().parent.parent
CONFIG = RADICE / "config.yaml"
WORKFLOW = RADICE / ".github" / "workflows" / "check.yml"

# Il blocco che questo script possiede, dall'inizio di `orologio:` fino alla
# riga vuota prima di `concurrency:`.
BLOCCO = re.compile(
    r"(?ms)^(?P<testa>[ ]*# --- MENU GENERATO.*?\n)?^(?P<ind>[ ]+)orologio:\n"
    r"(?:^(?P=ind)[ ]+.*\n|^[ ]*#.*\n|^\s*-[ ].*\n)*"
)


SEPARATORE = " — "      # trattino lungo: negli id non compare mai


def voci_dal_config() -> list[str]:
    """Le voci del menu, nella forma `Nome leggibile — id`.

    Il nome davanti e l'id dietro, e non il contrario, perche' la tendina di
    GitHub tronca le voci lunghe: la parte che si legge sempre e' la prima, e
    quella deve essere il nome dell'orologio. L'id resta in coda perche' e'
    quello che il programma usa davvero, e averlo sotto gli occhi evita di
    doverlo cercare nel config quando serve da riga di comando.
    """
    dati = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    voci = []
    for w in (dati.get("watches") or []):
        if not w.get("id"):
            continue
        # Il soprannome serve: senza, "Tudor Black Bay Chrono" compariva due
        # volte identico (Flamingo e Panda) e i due Speedmaster erano
        # indistinguibili. Una tendina con due voci uguali e' peggio di una
        # con le sigle.
        nome = " ".join(str(w.get(k, "")).strip()
                        for k in ("brand", "model", "nickname")).strip()
        voci.append(f"{nome}{SEPARATORE}{w['id']}" if nome else str(w["id"]))
    return voci


def blocco_nuovo(voci: list[str], ind: str = "      ") -> str:
    righe = [
        f"{ind}# --- MENU GENERATO da tools/aggiorna_menu.py — non modificare a mano.\n",
        f"{ind}# L'elenco qui sotto e' una copia di cio' che sta in config.yaml, e le\n",
        f"{ind}# copie divergono: era gia' successo coi gruppi di rotazione, e la\n",
        f"{ind}# scelta finiva scartata in silenzio. Lo riscrive `pubblica.sh`.\n",
        f"{ind}orologio:\n",
        f'{ind}  description: "Quali orologi controllare"\n',
        f"{ind}  type: choice\n",
        f"{ind}  default: tutti\n",
        f"{ind}  options:\n",
        f"{ind}    - tutti\n",
    ]
    # Fra virgolette: i nomi contengono spazi, parentesi e virgolette basse.
    righe += [f'{ind}    - "{v}"\n' for v in voci]
    return "".join(righe)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--verifica", action="store_true",
                   help="non scrive: esce con 1 se il menu e' disallineato")
    args = p.parse_args(argv)

    testo = WORKFLOW.read_text(encoding="utf-8")
    m = BLOCCO.search(testo)
    if not m:
        print("Non trovo il blocco `orologio:` nel workflow.", file=sys.stderr)
        return 2

    voci = voci_dal_config()
    nuovo = blocco_nuovo(voci, m.group("ind"))
    if m.group(0) == nuovo:
        if not args.verifica:
            print(f"menu gia' allineato ({len(voci)} orologi)")
        return 0

    if args.verifica:
        print(f"Il menu e' disallineato: nel config ci sono {len(voci)} orologi.",
              file=sys.stderr)
        return 1

    WORKFLOW.write_text(testo[:m.start()] + nuovo + testo[m.end():],
                        encoding="utf-8")
    print(f"menu aggiornato: tutti + {len(voci)} orologi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
