"""Rilevazioni di mercato: campioni raccolti a mano, per dare fondamento all'indice.

Il problema che risolve
-----------------------
Il radar vede solo cio' che passa dai suoi canali, e gli alert dei marketplace
scattano solo sugli annunci *nuovi*. Il risultato e' che dopo settimane l'indice
di ogni orologio poggia ancora su uno o due campioni — quando non su zero — e
quindi sul numero che ho scritto a mano nel config. Ogni punteggio che leggi e'
calcolato contro un'ipotesi, e le ipotesi si sono gia' rivelate sbagliate del
doppio e del triplo.

Una rilevazione e' una fotografia del mercato presa una volta, a mano, e messa
nel repository. Da quel momento l'indice ha decine di campioni veri invece di
un'ipotesi.

Le tre regole che la rendono innocua
------------------------------------
1. **Non sono annunci attivi** (`active = 0`): non compaiono in dashboard, non
   generano notifiche, non vengono mai proposti come occasioni. Servono solo
   come termine di paragone.
2. **Sono idempotenti**: reimportare lo stesso file non duplica niente. Il giro
   automatico puo' rileggerlo quattro volte al giorno senza conseguenze.
3. **Invecchiano**: l'indice guarda solo gli ultimi `lookback_days` giorni, per
   cui una rilevazione vecchia esce di scena da sola. E' voluto — un prezzo di
   sei mesi fa non deve pesare come uno di ieri.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import Listing

log = logging.getLogger("radar.rilevazione")

CARTELLA = "rilevazioni"


def importa(db, cfg, cartella: str | Path = CARTELLA) -> int:
    """Carica tutte le rilevazioni presenti. Ritorna quanti campioni nuovi."""
    p = Path(cartella)
    if not p.is_dir():
        return 0
    per_id = {w.id: w for w in cfg.watches}
    nuovi = 0
    for file in sorted(p.glob("*.json")):
        try:
            dati = json.loads(file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("rilevazione %s illeggibile: %s", file.name, exc)
            continue
        nuovi += _carica(db, per_id, dati, file.stem)
    if nuovi:
        log.info("%d campioni di mercato aggiunti dalle rilevazioni", nuovi)
    return nuovi


def _carica(db, per_id: dict, dati: dict, etichetta: str) -> int:
    fonte = str(dati.get("fonte", "rilevazione"))
    data = str(dati.get("data", etichetta))
    nuovi = 0
    for watch_id, campioni in (dati.get("campioni") or {}).items():
        w = per_id.get(watch_id)
        if w is None:
            log.debug("rilevazione per un orologio non piu' monitorato: %s", watch_id)
            continue
        # La referenza e' cio' su cui l'indice raggruppa i comparabili: deve
        # essere una di quelle dell'orologio, altrimenti il campione esiste
        # nel database ma non entra in nessun calcolo.
        riferimento = (w.references_esatte or w.references or [watch_id])[0]
        for i, c in enumerate(campioni):
            prezzo = c.get("p") if isinstance(c, dict) else None
            if not prezzo:
                continue
            l = Listing(
                source=fonte,
                url=f"rilevazione:{fonte}/{data}/{watch_id}/{i}",
                title=f"[rilevazione {data}] {w.label}",
                reference=riferimento,
                price_eur=float(prezzo),
                seller_country=(c.get("c") or None),
                year=c.get("a"),
                condition=c.get("s"),
                full_set=c.get("f"),
            )
            if db.get(l.key) is None:
                nuovi += 1
            db.upsert(l, watch_id)
            db.conn.execute("UPDATE listings SET active = 0 WHERE key = ?", (l.key,))
    db.conn.commit()
    return nuovi
