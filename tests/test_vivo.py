"""I link devono portare dove promettono, e quelli morti devono sparire.

Il 24/09/2026 la dashboard aveva 320 annunci Chrono24 dati per attivi, il piu'
vecchio di diciannove giorni. Cliccandone alcuni non si finiva sull'annuncio
ma sulla pagina generica del modello.

Non era un link scritto male: era un annuncio venduto. Chrono24 non risponde
"non esiste", reindirizza:

    /omega/speedmaster-professional-moonwatch--id47542099.htm
        diventa
    /omega/ref-31030425001002.htm

E nessuno se ne accorgeva perche' le fonti dei negozi si ripuliscono da sole
(si rilegge il catalogo, chi manca viene chiuso) mentre gli annunci che
arrivano via email si vedono una volta sola e poi piu' nessuno li richiama.

Gli indirizzi qui sotto sono veri, e i reindirizzamenti sono quelli osservati
navigandoli a mano quel giorno.
"""
import pytest

from radar import vivo


VIVO_19_GIORNI = ("https://www.chrono24.it/zenith/"
                  "zenith-elite-ultra-thin-or-rose--id48222829.htm")
VIVO_11_GIORNI = ("https://www.chrono24.it/omega/"
                  "omega-speedmaster-professional-moonwatch--id48406006.htm")
VENDUTO = ("https://www.chrono24.it/omega/"
           "speedmaster-professional-moonwatch--id47542099.htm")
MODELLO = "https://www.chrono24.it/omega/ref-31030425001002.htm"


class _Fetcher:
    """Finge la rete: mappa indirizzo chiesto → (indirizzo finale, codice)."""

    def __init__(self, mappa=None, silenzio=False):
        self.mappa = mappa or {}
        self.silenzio = silenzio
        self.chiamate = []

    def dove_porta(self, url):
        self.chiamate.append(url)
        if self.silenzio:
            return None, None, "anti-bot (challenge page)"
        finale, codice = self.mappa.get(url, (url, 200))
        return finale, codice, "ok"


def test_un_annuncio_venduto_si_riconosce_dal_reindirizzamento():
    f = _Fetcher({VENDUTO: (MODELLO, 200)})
    stato, perche = vivo.stato(VENDUTO, f)
    assert stato == vivo.SPARITO
    assert "ref-31030425001002" in perche


def test_un_annuncio_vivo_resta_dov_e():
    """Anche dopo diciannove giorni: l'eta' da sola non dice niente.

    Era la tentazione piu' facile — chiudere tutto quello che non si rivede da
    N giorni — ed e' sbagliata: questi due erano ancora perfettamente online.
    """
    f = _Fetcher()
    for u in (VIVO_19_GIORNI, VIVO_11_GIORNI):
        assert vivo.stato(u, f)[0] == vivo.VIVO, u


def test_quattrocentoquattro_e_sparito():
    f = _Fetcher({"https://www.chlw.it/products/x": ("https://www.chlw.it/products/x", 404)})
    assert vivo.stato("https://www.chlw.it/products/x", f)[0] == vivo.SPARITO


def test_nel_dubbio_non_si_tocca_niente():
    """Se non si riesce a chiedere, l'annuncio resta.

    Chrono24 da GitHub risponde con la schermata anti-bot. Il
    reindirizzamento si vede lo stesso — avviene prima, a livello di
    protocollo — ma se la richiesta fallisce del tutto non sappiamo niente, e
    cancellare un annuncio buono perche' la rete ha singhiozzato sarebbe
    esattamente il tipo di errore silenzioso che questo progetto paga caro.
    """
    muto = _Fetcher(silenzio=True)
    assert vivo.stato(VENDUTO, muto)[0] == vivo.BOH
    assert vivo.stato(VIVO_19_GIORNI, muto)[0] == vivo.BOH


def test_ripulisci_chiude_solo_gli_spariti(tmp_path):
    from radar.db import Database
    from radar.models import Listing

    db = Database(tmp_path / "t.db")
    vivi = [VIVO_19_GIORNI, VIVO_11_GIORNI]
    for u in vivi + [VENDUTO]:
        db.upsert(Listing(source="chrono24", url=u, price_eur=6500.0), "speedmaster")

    f = _Fetcher({VENDUTO: (MODELLO, 200)})
    controllati, chiusi, incerti = vivo.ripulisci(db, f, limite=10)

    assert (controllati, chiusi, incerti) == (3, 1, 0)
    attivi = {l["url"] for l in db.active_listings()}
    assert attivi == set(vivi)
    db.close()


def test_il_silenzio_della_rete_non_svuota_la_lista(tmp_path):
    """La prova piu' importante: se Chrono24 smette di rispondere del tutto,
    la dashboard non deve svuotarsi."""
    from radar.db import Database
    from radar.models import Listing

    db = Database(tmp_path / "t.db")
    for u in (VIVO_19_GIORNI, VIVO_11_GIORNI, VENDUTO):
        db.upsert(Listing(source="chrono24", url=u, price_eur=6500.0), "speedmaster")

    controllati, chiusi, incerti = vivo.ripulisci(db, _Fetcher(silenzio=True), limite=10)
    assert chiusi == 0 and incerti == 3
    assert len(db.active_listings()) == 3
    db.close()


def test_si_controllano_prima_i_piu_trascurati(tmp_path):
    """Un gruppo per giro, i mai controllati in testa.

    Cosi' un annuncio appena arrivato viene guardato al primo giro utile, e
    l'intero elenco si ripassa in qualche giorno senza che nessun giro debba
    fare centinaia di richieste.
    """
    from radar.db import Database
    from radar.models import Listing

    db = Database(tmp_path / "t.db")
    for i in range(6):
        db.upsert(Listing(source="chrono24",
                          url=f"https://www.chrono24.it/omega/x--id{i}.htm",
                          price_eur=6500.0), "speedmaster")

    f = _Fetcher()
    vivo.ripulisci(db, f, limite=2)
    primo_giro = set(f.chiamate)
    assert len(primo_giro) == 2

    f2 = _Fetcher()
    vivo.ripulisci(db, f2, limite=2)
    # Il secondo giro guarda altri due, non gli stessi.
    assert not (set(f2.chiamate) & primo_giro)
    db.close()
