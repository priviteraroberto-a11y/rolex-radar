"""Un annuncio e' ancora online, o e' solo un link che porta altrove?

Il problema
-----------
Le fonti dei negozi si ripuliscono da sole: a ogni giro si legge il catalogo
intero, e quello che non c'e' piu' viene chiuso. Chrono24 no. Li' il radar non
puo' leggere niente — c'e' la protezione anti-bot — e vede gli annunci una
volta sola, quando arriva l'alert via email. Dopo, nessuno li richiama piu'.

Risultato: al 24/09/2026 c'erano 320 annunci Chrono24 dati per attivi, il piu'
vecchio di 19 giorni, e buona parte era gia' stata venduta. Cliccando si
finiva su una pagina che non era quella promessa.

Come si riconosce un annuncio morto
-----------------------------------
Chrono24 non risponde "non esiste". Reindirizza alla pagina generica del
modello:

    /omega/speedmaster-professional-moonwatch--id47542099.htm
        diventa
    /omega/ref-31030425001002.htm

Cioe' l'indirizzo **perde il suo `--idNNNN.htm`**. E' un segnale netto e
verificato a mano su annunci veri: quelli vivi restano dove sono, anche dopo
diciannove giorni; quelli venduti finiscono tutti sulla pagina del modello.

Perche' funziona malgrado l'anti-bot
------------------------------------
Il contenuto della pagina, da GitHub, non si riesce a leggere: arriva la
schermata di controllo. Ma il **reindirizzamento avviene prima**, a livello di
protocollo, e l'indirizzo finale si vede comunque. Non serve leggere la
pagina: basta guardare dove si e' finiti.

La regola del dubbio
--------------------
Tre risposte possibili, e la terza conta quanto le altre due:

    vivo      l'indirizzo finale e' ancora quello di un annuncio
    sparito   l'indirizzo finale non e' piu' quello di un annuncio, o 404
    boh       non si e' riusciti a chiedere

Nel dubbio non si tocca niente. Cancellare un annuncio buono perche' la rete
ha singhiozzato e' un errore silenzioso, e in questo progetto gli errori
silenziosi sono gia' costati abbastanza.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from . import extract

log = logging.getLogger("radar.vivo")

VIVO, SPARITO, BOH = "vivo", "sparito", "boh"

# Il marcatore che rende un indirizzo Chrono24 un annuncio e non un modello.
_ID_CHRONO24 = re.compile(r"--id\d+\.htm", re.I)


def stato(url: str, fetcher) -> tuple[str, str]:
    """Dice se l'annuncio e' ancora al suo posto. Ritorna (stato, perche')."""
    if not url:
        return BOH, "senza indirizzo"

    finale, codice, dettaglio = fetcher.dove_porta(url)
    if finale is None:
        return BOH, dettaglio

    if codice in (404, 410):
        return SPARITO, f"HTTP {codice}"

    # Il caso Chrono24, e in generale: un annuncio che non e' piu' un annuncio.
    if _ID_CHRONO24.search(url) and not _ID_CHRONO24.search(finale):
        return SPARITO, f"reindirizzato a {urlparse(finale).path[:60]}"

    if not extract.e_url_di_annuncio(finale):
        return SPARITO, f"reindirizzato a {urlparse(finale).path[:60]}"

    return VIVO, "ok"


def ripulisci(db, fetcher, limite: int = 40) -> tuple[int, int, int]:
    """Controlla un po' di annunci attivi e chiude quelli spariti.

    Non li controlla tutti a ogni giro: sarebbero centinaia di richieste. Ne
    prende un gruppo per volta, i meno recentemente controllati per primi,
    cosi' in qualche giro il giro completo si chiude da solo.

    Ritorna (controllati, chiusi, incerti).
    """
    righe = db.da_verificare(limite)
    controllati = chiusi = incerti = 0
    for riga in righe:
        st, perche = stato(riga["url"], fetcher)
        controllati += 1
        if st == SPARITO:
            db.chiudi(riga["key"])
            chiusi += 1
            log.info("non piu' online: %s (%s)", riga["url"][:70], perche)
        elif st == BOH:
            incerti += 1
        db.segna_verificato(riga["key"])
    if controllati:
        log.info("verifica link: %d controllati, %d chiusi, %d incerti",
                 controllati, chiusi, incerti)
    return controllati, chiusi, incerti
