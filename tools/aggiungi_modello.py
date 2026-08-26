#!/usr/bin/env python3
"""Aggiunge un orologio a config.yaml partendo dal testo di una richiesta.

Lo esegue il workflow `nuovo-modello.yml` quando apri una richiesta dal form.

Due scelte che meritano una riga di spiegazione.

**L'inserimento e' testuale, non tramite libreria YAML.** Rileggere e riscrivere
il file con `yaml.dump` cancellerebbe tutti i commenti — e in questo config i
commenti sono meta' del valore: spiegano perche' una soglia e' quella e non
un'altra, e quali errori sono gia' costati caro. Quindi il blocco nuovo viene
infilato in fondo alla lista `watches:`, lasciando intatto tutto il resto.

**Il gruppo viene ricalcolato qui.** La pagina te ne mostra uno, ma fra il
momento in cui compili e quello in cui il workflow scrive puo' essere cambiato
qualcosa. L'ultima parola ce l'ha chi scrive.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

BLOCCO = re.compile(r"```(?:ya?ml)?\s*\n(.*?)```", re.S | re.I)


def estrai_blocco(testo: str) -> str:
    """Il primo blocco recintato della richiesta."""
    m = BLOCCO.search(testo or "")
    if not m:
        raise ValueError(
            "Non trovo nessun blocco ```yaml``` nella richiesta. "
            "Usa il modulo su .../nuovo.html invece di scrivere a mano."
        )
    return m.group(1).rstrip() + "\n"


def valida(blocco: str, config: dict) -> dict:
    """Il blocco e' un orologio sensato e nuovo?

    Meglio rifiutare con una spiegazione che scrivere qualcosa di rotto: un
    config non valido ferma tutti i giri, non solo quello dell'orologio nuovo.
    """
    try:
        dati = yaml.safe_load(blocco)
    except yaml.YAMLError as exc:
        raise ValueError(f"Il blocco non e' YAML valido: {exc}") from exc

    if not isinstance(dati, list) or len(dati) != 1 or not isinstance(dati[0], dict):
        raise ValueError("Il blocco deve contenere esattamente un orologio.")
    w = dati[0]

    for campo in ("id", "brand", "model"):
        if not str(w.get(campo, "")).strip():
            raise ValueError(f"Manca il campo obbligatorio `{campo}`.")

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", str(w["id"])):
        raise ValueError(f"L'identificativo `{w['id']}` non e' valido: "
                         "solo minuscole, cifre e trattini.")

    esistenti = {str(x.get("id")) for x in (config.get("watches") or [])}
    if str(w["id"]) in esistenti:
        raise ValueError(f"L'orologio `{w['id']}` c'e' gia'.")

    per_nome = str(w.get("identify_by", "reference")).lower() == "name"
    if per_nome:
        if not w.get("must_include"):
            raise ValueError("Riconoscimento per nome senza `must_include`: "
                             "sarebbe invisibile.")
    elif not w.get("references"):
        raise ValueError("Nessuna referenza e nessun `must_include`: "
                         "l'orologio non sarebbe riconoscibile.")

    seed = w.get("fair_value", {}).get("seed_price_eur")
    if not seed or float(seed) <= 0:
        raise ValueError("Manca il prezzo indicativo (`seed_price_eur`).")

    hf = w.get("hard_filters", {}) or {}
    lo = float(hf.get("absolute_min_price_eur", 0))
    hi = float(hf.get("absolute_max_price_eur", 0))
    # Le stesse guardie dei test: una banda stretta non protegge da niente e
    # nasconde in silenzio proprio l'affare che cercavi.
    if lo > float(seed) * 0.55:
        raise ValueError(f"La soglia minima ({lo:,.0f}) e' troppo vicina alla "
                         f"stima ({float(seed):,.0f}): un affare vero verrebbe scartato.")
    if hi < float(seed) * 1.8:
        raise ValueError(f"Il tetto ({hi:,.0f}) e' troppo basso rispetto alla "
                         f"stima ({float(seed):,.0f}).")
    return w


def gruppo_meno_affollato(config: dict) -> str:
    gruppi = [str(g) for g in ((config.get("rotation") or {}).get("groups") or [])]
    if not gruppi:
        return ""
    c = Counter(str(w.get("group") or "") for w in (config.get("watches") or []))
    return min(gruppi, key=lambda g: (c.get(g, 0), gruppi.index(g)))


def con_gruppo(blocco: str, gruppo: str) -> str:
    """Impone il gruppo deciso qui, sostituendo quello proposto dalla pagina."""
    if not gruppo:
        return blocco
    if re.search(r"^\s{4}group:\s*\S+", blocco, re.M):
        return re.sub(r"^\s{4}group:.*$", f"    group: {gruppo}", blocco,
                      count=1, flags=re.M)
    righe = blocco.splitlines()
    righe.insert(1, f"    group: {gruppo}")
    return "\n".join(righe) + "\n"


def inserisci(testo_config: str, blocco: str) -> str:
    """Infila il blocco in fondo alla lista `watches:`, conservando i commenti."""
    righe = testo_config.splitlines(keepends=True)
    inizio = next((i for i, r in enumerate(righe) if re.match(r"^watches:\s*$", r)), None)
    if inizio is None:
        raise ValueError("Non trovo la sezione `watches:` in config.yaml.")

    fine = len(righe)
    for i in range(inizio + 1, len(righe)):
        if re.match(r"^[A-Za-z_][\w-]*:", righe[i]):     # prossima sezione radice
            fine = i
            break
    # a ritroso oltre i commenti e le righe vuote che introducono la sezione dopo
    while fine - 1 > inizio and righe[fine - 1].strip() in ("",) or (
            fine - 1 > inizio and righe[fine - 1].lstrip().startswith("#")
            and righe[fine - 1].startswith("#")):
        fine -= 1

    pezzo = "\n" + blocco.rstrip("\n") + "\n\n"
    return "".join(righe[:fine]) + pezzo + "".join(righe[fine:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--body", required=True, help="file col testo della richiesta")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out-message", default="", help="dove scrivere l'esito")
    args = ap.parse_args()

    percorso = Path(args.config)
    testo = percorso.read_text(encoding="utf-8")
    config = yaml.safe_load(testo) or {}

    try:
        blocco = estrai_blocco(Path(args.body).read_text(encoding="utf-8"))
        w = valida(blocco, config)
        gruppo = gruppo_meno_affollato(config)
        blocco = con_gruppo(blocco, gruppo)
        nuovo = inserisci(testo, blocco)
        # riprova a leggerlo: meglio accorgersene qui che al prossimo giro
        riletto = yaml.safe_load(nuovo)
        ids = [str(x.get("id")) for x in (riletto.get("watches") or [])]
        if str(w["id"]) not in ids:
            raise ValueError("L'inserimento non ha prodotto un config leggibile.")
    except ValueError as exc:
        esito = f"Non ho potuto aggiungerlo.\n\n**{exc}**"
        print(esito, file=sys.stderr)
        if args.out_message:
            Path(args.out_message).write_text(esito, encoding="utf-8")
        return 1

    percorso.write_text(nuovo, encoding="utf-8")
    esito = (f"Aggiunto **{w['brand']} {w['model']}** come `{w['id']}`, "
             f"nel turno `{gruppo}`.\n\n"
             f"Comparira' in dashboard al prossimo giro. Se il prezzo "
             f"indicativo si rivela sbagliato, correggilo in `config.yaml`: "
             f"e' il singolo intervento che migliora di piu' i punteggi.")
    print(esito)
    if args.out_message:
        Path(args.out_message).write_text(esito, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
