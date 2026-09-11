"""Espansione di `{page}` negli indirizzi di un catalogo.

Perche' non si elencano le pagine a mano
----------------------------------------
Nel config c'erano due righe cosi':

    - "https://www.universo-oro.it/api/public/watches?page=1&limit=100"
    - "https://www.universo-oro.it/api/public/watches?page=2&limit=100"

Sono giuste finche' il negozio ha meno di duecento orologi. Il giorno che ne
mette il duecentounesimo, quello e tutti i successivi smettono di esistere per
il radar. Non compare un errore: la seconda pagina risponde regolarmente, e' il
catalogo che e' diventato piu' lungo dell'elenco. E' lo stesso modo di perdere
un orologio della ricerca che restituisce i primi dieci — solo piu' lento ad
arrivare.

Scrivendo `page={page}` e lasciando che sia il ciclo a fermarsi quando una
pagina torna vuota, il radar segue il catalogo dovunque vada.

`MAX_PAGINE` non e' una scelta di quante pagine leggere: e' la garanzia che un
sito che risponde sempre la stessa cosa non tenga il giro in ostaggio.
"""
from __future__ import annotations

MAX_PAGINE = 40


def serie(cfg: dict) -> list[list[str]]:
    """Gli indirizzi da leggere, raggruppati per catalogo.

    Ogni gruppo e' una successione da percorrere in ordine e da interrompere
    appena una pagina torna vuota. Il raggruppamento non e' un dettaglio: una
    fonte puo' avere piu' cataloghi indipendenti — le sette pagine di marca di
    della Rocca, per esempio — e la fine di uno non dice niente sugli altri.
    Con un elenco piatto, il primo catalogo esaurito spegnerebbe anche i sei
    che vengono dopo.

    Un indirizzo senza `{page}` e' un gruppo da una pagina sola.
    """
    pg = cfg.get("paginate") or {}
    da = int(pg.get("da", 1))
    fino = min(int(pg.get("max", MAX_PAGINE)), MAX_PAGINE)

    fuori: list[list[str]] = []
    for url in cfg.get("start_urls", []):
        if "{page}" not in str(url):
            fuori.append([url])
        else:
            fuori.append([str(url).replace("{page}", str(n))
                          for n in range(da, da + fino)])
    return fuori
