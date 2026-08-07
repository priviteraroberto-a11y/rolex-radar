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
