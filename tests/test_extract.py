"""Test dell'estrazione: è il punto dove il sistema può sbagliare in silenzio."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import extract  # noqa: E402


# --- prezzo -------------------------------------------------------------------

def test_prezzo_formato_italiano():
    assert extract.parse_price("23.700 €") == (23700.0, "EUR")
    assert extract.parse_price("€ 29.900,00") == (29900.0, "EUR")
    assert extract.parse_price("EUR 31 500") == (31500.0, "EUR")


def test_prezzo_formato_anglosassone():
    assert extract.parse_price("$32,900") == (32900.0, "USD")
    assert extract.parse_price("£27,450.00") == (27450.0, "GBP")
    assert extract.parse_price("CHF 30'800") == (30800.0, "CHF")


def test_prezzo_su_richiesta():
    assert extract.parse_price("Prezzo su richiesta")[0] is None
    assert extract.parse_price("Price on request")[0] is None


def test_prezzo_non_confonde_anno_e_referenza():
    amount, _ = extract.parse_price("Rolex 126710BLRO del 2025 — 33.500 €")
    assert amount == 33500.0


def test_conversione_valuta():
    assert extract.to_eur(10000, "USD") == 9200.0
    assert extract.to_eur(None, "USD") is None


# --- referenza ----------------------------------------------------------------

def test_referenza():
    assert extract.parse_reference("Rolex GMT-Master II 126710BLRO") == "126710BLRO"
    assert extract.parse_reference("ref. 126710 BLRO Pepsi") == "126710BLRO"


def test_match_referenza_tollerante():
    w = ["126710BLRO"]
    assert extract.matches_reference(None, "GMT Master II ref 126710BLRO", w)
    assert extract.matches_reference(None, "126710-BLRO Pepsi jubilee", w)
    assert not extract.matches_reference(None, "Submariner 126610LN", w)


# --- anno ---------------------------------------------------------------------

def test_anno_con_contesto():
    assert extract.parse_year("Anno 2025, full set") == 2025
    assert extract.parse_year("Year of production: 2024") == 2024
    assert extract.parse_year("garanzia 2023 italiana") == 2023


def test_anno_ignora_numeri_lunghi():
    # 126710 non deve produrre "2671" o simili, e il seriale nemmeno
    assert extract.parse_year("Ref 126710BLRO serial 89203847") is None


# --- bracciale, condizione, corredo -------------------------------------------

def test_bracciale():
    assert extract.parse_bracelet("bracciale Jubilee originale") == "jubilee"
    assert extract.parse_bracelet("Jubilée bracelet") == "jubilee"
    assert extract.parse_bracelet("Oyster bracelet steel") == "oyster"
    # "Oyster Perpetual" è il nome del modello, non del bracciale
    assert extract.parse_bracelet("Rolex Oyster Perpetual GMT") is None


def test_condizione():
    assert extract.parse_condition("Nuovo mai indossato") == "unworn"
    assert extract.parse_condition("Unworn, stickered") == "unworn"
    assert extract.parse_condition("Ottime condizioni") == "excellent"
    assert extract.parse_condition("come nuovo") == "mint"


def test_full_set():
    assert extract.parse_full_set("Full set, scatola e garanzia") is True
    assert extract.parse_full_set("box and papers") is True
    assert extract.parse_full_set("solo orologio, senza scatola") is False
    assert extract.parse_full_set("bellissimo orologio") is None


def test_mai_lucidato():
    assert extract.parse_never_polished("mai lucidato, smussi intatti") is True
    assert extract.parse_never_polished("never polished") is True
    assert extract.parse_never_polished("cassa lucidata di recente") is False


# --- garanzia -----------------------------------------------------------------

def test_garanzia():
    assert extract.parse_warranty_region("garanzia italiana 2025") == "IT"
    assert extract.parse_warranty_region("warranty card UAE Dubai") == "AE"
    assert extract.parse_warranty_region("Saudi Arabia warranty") == "SA"
    assert extract.parse_warranty_region("un bell'orologio") is None


def test_normalizzazione_regione():
    assert extract.normalize_region_group("DE") == "EU"
    assert extract.normalize_region_group("IT") == "IT"
    assert extract.normalize_region_group("AE") == "AE"


# --- integrazione -------------------------------------------------------------

def test_enrich_annuncio_realistico():
    from radar.models import Listing
    l = Listing(
        source="demo",
        url="https://demo.it/1",
        title="Rolex GMT-Master II 126710BLRO Pepsi Jubilee 2025",
        raw_text=("Anno 2025, unworn mai indossato, full set con scatola e "
                  "garanzia italiana, mai lucidato. Prezzo 33.900 €"),
    )
    extract.enrich(l, {"seller_trust": 4, "dealer": True, "country": "IT"})
    assert l.reference == "126710BLRO"
    assert l.year == 2025
    assert l.bracelet == "jubilee"
    assert l.condition == "unworn"
    assert l.full_set is True
    assert l.never_polished is True
    assert l.warranty_region == "IT"
    assert l.price_eur == 33900.0
    assert l.seller_trust == 4


# --- protezione contro lo spam SEO --------------------------------------------

WANTED = ["126710BLRO"]
KEYWORDS = ["GMT-Master", "GMT Master", "GMT", "Pepsi"]


def test_datejust_con_spam_seo_viene_scartato():
    """Caso reale: della Rocca appiccica '126710BLRO' a schede di altri modelli."""
    titolo = "Rolex Datejust 126234 Jubilee Quadrante Rosa Romani"
    corpo = ("Rolex Datejust - Ref. 126234 - Condizioni: Nuovo - Bracciale "
             "jubilé - Scatola e garanzia. 126710BLRO. Maggiori informazioni")
    assert not extract.is_target_watch(titolo, f"{titolo} {corpo}", WANTED, KEYWORDS)


def test_referenza_nel_titolo_viene_accettata():
    t = "Rolex GMT-Master II 126710BLRO Pepsi Jubilee 2025"
    assert extract.is_target_watch(t, t, WANTED, KEYWORDS)


def test_referenza_solo_nel_corpo_ma_titolo_giusto():
    t = "Rolex GMT-Master II Pepsi"
    assert extract.is_target_watch(t, f"{t} ref 126710BLRO anno 2025", WANTED, KEYWORDS)


def test_referenza_solo_nel_corpo_e_titolo_generico():
    t = "Orologio di lusso in vendita a Bologna"
    assert not extract.is_target_watch(t, f"{t} 126710BLRO", WANTED, KEYWORDS)


def test_titolo_povero_si_affida_al_corpo():
    t = "Vedi"
    assert extract.is_target_watch(t, "Rolex 126710BLRO Pepsi", WANTED, KEYWORDS)


def test_submariner_resta_escluso():
    t = "Rolex Submariner 126610LN 2024"
    assert not extract.is_target_watch(t, f"{t} 126710BLRO", WANTED, KEYWORDS)


def test_altra_referenza_nel_titolo():
    assert extract.other_reference_in_title("Rolex Datejust 126234", WANTED)
    assert not extract.other_reference_in_title("Rolex GMT-Master II 126710BLRO", WANTED)
    assert not extract.other_reference_in_title("Rolex GMT-Master II Pepsi", WANTED)


# --- disponibilità ------------------------------------------------------------

def test_venduto_riconosciuto():
    assert extract.parse_sold("Prodotto Non Disponibile")
    assert extract.parse_sold("VENDUTO")
    assert extract.parse_sold("Sold Out")
    assert extract.parse_sold("Esaurito")
    assert not extract.parse_sold("Disponibile, spedizione immediata")


def test_enrich_marca_il_venduto():
    from radar.models import Listing
    l = Listing(source="d", url="https://d.it/1",
                title="Rolex GMT-Master II 126710BLRO",
                raw_text="Prodotto Non Disponibile 33.000 €")
    extract.enrich(l, {})
    assert l.sold is True


# --- mittenti con intestazioni codificate ------------------------------------

def test_match_sender_con_header_codificato():
    """Le intestazioni non ASCII arrivano codificate in base64: il nome sparisce."""
    import base64
    from email.header import decode_header, make_header
    from radar.sources.email_source import EmailSource

    class Ctx:
        config = None

    src = EmailSource({"name": "e", "from_contains": ["chrono24", "subito"]}, Ctx())

    nome = "Chrono24 Servizio Clienti"
    b64 = base64.b64encode(nome.encode()).decode()
    grezzo = f"=?UTF-8?B?{b64}?= <noreply@mailer.c24.com>"

    # nella stringa grezza "chrono24" non compare: e' dentro il base64
    assert src._match_sender(grezzo.lower()) is None

    # decodificando invece si trova
    decodificato = str(make_header(decode_header(grezzo)))
    assert src._match_sender(f"{decodificato} {grezzo}".lower()) == "chrono24"

    # i mittenti normali continuano a funzionare
    assert src._match_sender("chrono24 <service@chrono24.com>") == "chrono24"
    assert src._match_sender("zno <newsletter@e.zno.com>") is None


# --- Batman vs Pepsi: stesse 6 cifre, orologi diversi -------------------------

EXCL = ["BLNR", "Batman", "Sprite", "Root Beer"]


def test_batman_non_passa_per_pepsi():
    """126710BLNR e 126710BLRO condividono le prime 6 cifre. Caso reale."""
    titolo = "Rolex GMT-Master II 126710BLNR Batman Jubilee 2026"
    corpo = f"{titolo} vedi anche Rolex GMT-Master II 126710BLRO Pepsi"
    assert not extract.is_target_watch(titolo, corpo, WANTED, KEYWORDS, EXCL)
    assert extract.other_reference_in_title(titolo, WANTED)


def test_batman_senza_referenza_nel_titolo():
    titolo = "Rolex GMT-Master II Batman 40mm"
    corpo = f"{titolo} 126710BLRO"
    assert not extract.is_target_watch(titolo, corpo, WANTED, KEYWORDS, EXCL)


def test_pepsi_continua_a_passare():
    t = "Rolex GMT-Master II 126710BLRO Pepsi Jubilee 2025"
    assert extract.is_target_watch(t, t, WANTED, KEYWORDS, EXCL)
    assert not extract.other_reference_in_title(t, WANTED)

    t2 = "Rolex GMT Master II 126710 BLRO 40mm"
    assert extract.is_target_watch(t2, t2, WANTED, KEYWORDS, EXCL)


def test_suffisso_diverso_riconosciuto():
    assert extract.other_reference_in_title("Rolex GMT-Master II 126710BLNR", WANTED)
    assert extract.other_reference_in_title("Ref. 126711CHNR Root Beer", WANTED)
    assert not extract.other_reference_in_title("Rolex 126710BLRO", WANTED)
    # senza suffisso resta ambiguo: non si scarta
    assert not extract.other_reference_in_title("Rolex GMT-Master II 126710", WANTED)


# --- SMTP_PORT vuoto ----------------------------------------------------------

def test_smtp_port_vuoto_non_crasha(monkeypatch):
    """Su GitHub Actions un secret non definito arriva come stringa vuota."""
    from radar.notify.email_report import EmailNotifier
    monkeypatch.setenv("SMTP_PORT", "")
    monkeypatch.setenv("SMTP_HOST", "")
    n = EmailNotifier()
    assert n.port == 587
    assert n.enabled is False

    monkeypatch.setenv("SMTP_PORT", "465")
    assert EmailNotifier().port == 465


# --- referenza scritta in ordine invertito ------------------------------------

def test_referenza_invertita():
    """L'Orologiaio di Corte scrive 'GMT MASTER II BLRO REF. 126710'."""
    t = "GMT MASTER II BLRO REF. 126710"
    assert extract.matches_reference(None, t, WANTED)
    assert extract.is_target_watch(t, t, WANTED, KEYWORDS, EXCL)


def test_referenza_invertita_non_confonde_il_batman():
    t = "GMT MASTER II BLNR REF. 126710"
    assert not extract.matches_reference(None, t, WANTED)
    assert not extract.is_target_watch(t, t, WANTED, KEYWORDS, EXCL)


def test_referenza_invertita_rootbeer():
    t = 'GMT MASTER II "ROOTBEER" REF. 126711 CHNR 40 MM'
    assert not extract.is_target_watch(t, t, WANTED, KEYWORDS, EXCL)


def test_forme_separate():
    for t in ("Rolex 126710BLRO", "Rolex 126710 BLRO", "Rolex ref. 126710-BLRO"):
        assert extract.matches_reference(None, t, WANTED), t


# --- prezzo vs numero di referenza -------------------------------------------

def test_referenza_non_scambiata_per_prezzo():
    """Caso reale da L'Orologiaio di Corte: 'REF. 116234' diventava 116.234 €."""
    assert extract.parse_price("DATEJUST REF. 116234 36 MM.")[0] is None
    assert extract.parse_price("SUBMARINER REF. 126610 LN")[0] is None
    assert extract.parse_price("GMT MASTER II BLRO REF. 126710")[0] is None
    # numero seguito da suffisso di lettere: e' una referenza
    assert extract.parse_price("Rolex Gmt-Master II 126710BLRO Leggi di piu")[0] is None


def test_prezzo_vince_sulla_referenza_quando_ci_sono_entrambi():
    """Caso reale da Conte Orologi."""
    t = "Rolex GMT-Master II Pepsi Jubilee REF: 126710BLRO Disponibile 20499 € 20.499"
    assert extract.parse_price(t)[0] == 20499.0
    assert extract.parse_price("GMT MASTER II BLRO REF. 126710 - 22.000,00 €")[0] == 22000.0


def test_valuta_adiacente_ha_la_precedenza():
    """Se c'e' un numero attaccato alla valuta, gli altri numeri si ignorano."""
    t = "Rolex 126710BLRO cassa 40 mm calibro 3285 anno 2025 — 33.900 €"
    assert extract.parse_price(t)[0] == 33900.0


# --- referenze di altri marchi: stessa forma, valore diverso -------------------

SPEED = ["310.30.42.50.01.002"]


def test_speedmaster_sbagliato_scartato():
    """Caso reale: cercando il 310.30.42.50.01.002 e' arrivato il Moonwatch
    bianco 310.30.42.50.04.001, perche' la referenza giusta compariva fra i
    prodotti correlati della sua scheda."""
    giusto = "Omega Speedmaster Moonwatch Professional 310.30.42.50.01.002"
    bianco = "Omega Speedmaster Moonwatch Professional White 42mm 310.30.42.50.04.001"
    kw = ["Speedmaster", "Moonwatch"]

    assert extract.is_target_watch(giusto, giusto, SPEED, kw, [])
    assert not extract.is_target_watch(
        bianco, bianco + " vedi anche 310.30.42.50.01.002", SPEED, kw, [])


def test_la_forma_della_referenza():
    assert extract._forma("310.30.42.50.01.002") == r"\d{3}\.\d{2}\.\d{2}\.\d{2}\.\d{2}\.\d{3}"
    assert extract._forma("126710BLRO") == r"\d{6}[A-Z]{4}"
    assert extract._forma("4520V/210A-B128") == r"\d{4}[A-Z]{1}/\d{3}[A-Z]{1}\-[A-Z]{1}\d{3}"


def test_stessa_forma_diverso_valore_su_ogni_marchio():
    prove = [
        (["4520V/210A-B128"], "Vacheron Overseas 4520V/110A-B128"),
        (["15450ST.OO.1256ST.03"], "AP Royal Oak 15450ST.OO.1256ST.01"),
        (["03.A384.400/3817.M3817"], "Zenith A384 03.A384.400/3818.M3817"),
        (["M79360N-0024"], "Tudor Black Bay Chrono M79360N-0002"),
    ]
    for wanted, titolo in prove:
        assert extract.altra_referenza_stessa_forma(titolo, wanted), titolo
        assert not extract.altra_referenza_stessa_forma(
            titolo.replace(titolo.split()[-1], wanted[0]), wanted)


def test_senza_referenza_nel_testo_non_passa():
    t = "Omega Speedmaster Moonwatch Professional"
    assert not extract.is_target_watch(t, t, SPEED, ["Speedmaster"], [])


# --- quali email guardare -----------------------------------------------------

def test_criteri_imap_per_finestra_temporale():
    """Il filtro per data e' robusto: non dipende da chi ha aperto cosa."""
    from datetime import date, timedelta
    from radar.sources.email_source import EmailSource

    class Ctx:
        config = None

    da = date.today() - timedelta(days=7)
    mesi = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    atteso = f"(SINCE {da.day:02d}-{mesi[da.month - 1]}-{da.year})"

    assert EmailSource({"since_days": 7}, Ctx())._criteri() == atteso
    assert EmailSource({"unseen_only": True}, Ctx())._criteri() == "(UNSEEN)"
    assert EmailSource({}, Ctx())._criteri() == "(ALL)"
    # combinati
    c = EmailSource({"since_days": 7, "unseen_only": True}, Ctx())._criteri()
    assert c.startswith("(UNSEEN SINCE ")


# --- email: confini fra un annuncio e l'altro ---------------------------------

_EMAIL_C24 = """
<html><body>
 <table>
  <tr><td class="ad">
    <a href="https://www.chrono24.it/omega/speedmaster--id48015818.htm?eeid=XX&ikterm=AdImageLink">
      Omega Speedmaster Professional 310.30.42.50.01.002</a>
    <span>7.600 &euro; + 50 &euro; di spese di spedizione IT</span>
  </td></tr>
  <tr><td class="ad">
    <a href="https://www.chrono24.it/omega/speedmaster--id48015999.htm?eeid=XX&ikterm=AdImageLink">
      Omega Speedmaster Professional 310.30.42.50.01.002 2026 new full set</a>
    <span>5.612 &euro; Spedizione gratuita DE</span>
  </td></tr>
  <tr><td class="footer">
    <a href="https://www.chrono24.com/user/searchtasks.htm?eeid=XX&goal_searchtask_mail=1&ikterm=edit-saved-search">
      Modifica la tua ricerca salvata</a>
  </td></tr>
 </table>
</body></html>
"""


def _annunci_email(html_body):
    from bs4 import BeautifulSoup
    from radar.sources.email_source import EmailSource

    from radar.config import Config
    cfg = Config({"watches": [{"id": "speedmaster", "brand": "Omega",
                              "references": ["310.30.42.50.01.002"]}]})

    class Ctx:
        config = cfg

    src = EmailSource({}, Ctx())
    soup = BeautifulSoup(html_body, "lxml")
    return list(src._parse_html_email("chrono24", soup, cfg.references))


def test_il_bottone_modifica_ricerca_non_e_un_annuncio():
    """Il bug del 19/08: 'Modifica ricerca salvata' era finito in dashboard
    come Speedmaster a 5.612 euro sotto mercato del 25%. Il link non porta a
    nessun orologio e il prezzo era rubato all'annuncio accanto."""
    urls = [l.url for l in _annunci_email(_EMAIL_C24)]
    assert not any("searchtask" in u for u in urls), urls


def test_ogni_annuncio_tiene_il_proprio_prezzo():
    ann = {l.url.rsplit("--", 1)[-1]: l.raw_price for l in _annunci_email(_EMAIL_C24)}
    assert len(ann) == 2, ann
    assert "7.600" in ann["id48015818.htm"]
    assert "5.612" in ann["id48015999.htm"]


def test_i_link_chrono24_arrivano_puliti():
    """Quello che ti arriva su Telegram dev'essere condivisibile."""
    for l in _annunci_email(_EMAIL_C24):
        assert "?" not in l.url and "eeid" not in l.url, l.url


# --- provenienza negli alert Chrono24 -----------------------------------------

def test_il_paese_in_coda_ai_titoli_chrono24():
    """Il canale principale dichiara il paese del venditore in fondo al titolo.
    Senza leggerlo, tutto risultava 'ignoto' e la regola di ripiego lo trattava
    come europeo: annunci giapponesi promossi a 'vicini'."""
    casi = {
        "Rolex GMT-Master II 126710BLRO 20.999 € + 59 € di spese di spedizione FR": "FR",
        "Rolex GMT-Master II 126710BLRO 23.495 € Spedizione gratuita JP": "JP",
        "Vacheron Constantin 222 blue 4200H/222A-B934 2026/01 45.353 € + 111 € di spese di spedizione JP": "JP",
        "Omega Speedmaster 6.086 € + 113 € di spese di spedizione HK": "HK",
        "Tudor Black Bay Chrono 8.800 € + 30 € di spese di spedizione IT": "IT",
    }
    for titolo, atteso in casi.items():
        assert extract.parse_location(titolo) == atteso, titolo


def test_niente_paese_dove_non_ce_n_e():
    assert extract.parse_location("Omega Speedmaster 7.600 €") is None
    assert extract.parse_location("") is None


# --- riconoscere un orologio dal nome -----------------------------------------

def test_lo_zenith_elite_si_riconosce_dal_nome():
    """Il caso reale: quattro annunci su Chrono24, zero nel radar, perche'
    nessuno scrive la referenza 18.2010.681/01.C498 nel titolo."""
    reali = [
        "Zenith Elite Classic Automatic Ultra Thin",
        "Zenith Elite Ultra Thin Lady 33mm acciaio",
        "ZENITH ELITE 6150 ULTRA THIN 42MM",
    ]
    for titolo in reali:
        assert extract.matches_by_name(
            titolo, "", "Zenith", ["Elite", "Ultra Thin"],
            ["Defy", "Pilot", "Chronomaster"]), titolo


def test_il_nome_non_apre_le_porte_a_tutti_gli_zenith():
    must = ["Elite", "Ultra Thin"]
    esclusi = ["Defy", "Pilot", "Chronomaster"]
    for titolo in ["Zenith Elite Chronomaster Open",     # parola esclusa
                   "Zenith Elite 6150 42mm",             # manca 'Ultra Thin'
                   "Zenith Defy Skyline Ultra Thin",     # parola esclusa
                   "Omega De Ville Ultra Thin Elite"]:   # marca sbagliata
        assert not extract.matches_by_name(titolo, "", "Zenith", must, esclusi), titolo


def test_lo_scarto_dice_quale_parola_manca():
    """`inspect` deve spiegare, non solo rifiutare."""
    from radar.config import Config
    from radar.main import reject_reason
    from radar.models import Listing
    cfg = Config({"watches": [{
        "id": "zenith-ultrathin", "brand": "Zenith", "identify_by": "name",
        "must_include": ["Elite", "Ultra Thin"], "references": [],
        "exclude_keywords": ["Defy"],
    }]})
    w = cfg.watches[0]
    assert reject_reason(Listing(source="x", url="https://a/1",
                                 title="Zenith Elite 6150 42mm"), w) == \
        "nome incompleto, manca: Ultra Thin"
    assert reject_reason(Listing(source="x", url="https://a/2",
                                 title="Zenith Elite Classic Automatic Ultra Thin",
                                 price_eur=6900.0), w) is None
