"""Estrazione dei campi dal testo libero di un annuncio.

Questo è il modulo che fa la differenza fra un aggregatore di link e uno
strumento che capisce cosa sta guardando. Lavora su titolo + descrizione
in italiano e inglese.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Optional

# --- valute -------------------------------------------------------------------
# Tassi di ripiego, usati solo se non arriva nulla di più preciso.
FX_TO_EUR = {
    "EUR": 1.0, "USD": 0.92, "GBP": 1.17, "CHF": 1.05,
    "AED": 0.25, "JPY": 0.0059, "HKD": 0.118, "SGD": 0.68,
}

CURRENCY_TOKENS = [
    (r"€|eur\b|euro\b", "EUR"),
    (r"\$|usd\b", "USD"),
    (r"£|gbp\b", "GBP"),
    (r"chf\b|fr\.", "CHF"),
    (r"aed\b|dhs\b", "AED"),
    (r"¥|jpy\b", "JPY"),
]

REF_RE = re.compile(r"\b(1267\s?1[09]\s?BLRO|1267\s?10\s?BLNR|1267\s?10)\b", re.I)

_CURRENT_YEAR = datetime.now().year


def norm(text: str) -> str:
    """Normalizza per il matching: minuscolo, senza accenti, spazi compattati."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).lower().strip()


# =============================================================================
# PREZZO
# =============================================================================

_NUM = r"\d{1,3}(?:[.\s,']\d{3})+(?:[.,]\d{1,2})?|\d{4,7}(?:[.,]\d{1,2})?"
_CUR = r"(?:[€$£]|\beur\b|\busd\b|\bgbp\b|\bchf\b|\baed\b)"

# Un numero adiacente a un simbolo di valuta è quasi certamente un prezzo.
_PRICE_WITH_CUR = re.compile(rf"{_CUR}\s*({_NUM})|({_NUM})\s*{_CUR}", re.I)

# Un numero preceduto da "ref"/"art"/"cod", o seguito da un suffisso di lettere,
# è una referenza. Senza questo, "REF. 116234" diventa un prezzo di 116.234 €.
_REF_NUMBER = re.compile(
    rf"(?:\bref\.?|\breferenza\b|\breference\b|\bmodello\b|\bart\.?|\bcod\.?|\bn\.)"
    rf"\s*[:.]?\s*({_NUM})|({_NUM})\s*[A-Z]{{2,6}}\b",
    re.I,
)


def parse_price(text: str) -> tuple[Optional[float], str]:
    """Ritorna (importo, valuta). Gestisce 23.700 € / €23,700 / EUR 23 700.

    Regola: se nel testo c'è anche un solo numero attaccato a un simbolo di
    valuta, si considerano SOLO quelli. È la differenza fra leggere un prezzo
    e leggere un numero di referenza.
    """
    if not text:
        return None, "EUR"
    t = norm(text)

    if re.search(r"(prezzo su richiesta|price on request|poa|su richiesta|contattaci)", t):
        return None, "EUR"

    currency = "EUR"
    for pattern, code in CURRENCY_TOKENS:
        if re.search(pattern, t):
            currency = code
            break

    # numeri che sono referenze, non prezzi
    banned: set[float] = set()
    for m in _REF_NUMBER.finditer(text or ""):
        raw = m.group(1) or m.group(2)
        v = _to_float(raw) if raw else None
        if v is not None:
            banned.add(v)

    def usable(values: list[float]) -> list[float]:
        return [v for v in values
                if 500 <= v <= 5_000_000 and v not in banned
                and not (1900 <= v <= _CURRENT_YEAR + 2 and v == int(v))]

    # 1) numeri adiacenti a una valuta: i più affidabili
    with_cur: list[float] = []
    for m in _PRICE_WITH_CUR.finditer(t):
        v = _to_float(m.group(1) or m.group(2) or "")
        if v is not None:
            with_cur.append(v)
    good = usable(with_cur)
    if good:
        return max(good), currency

    # 2) ripiego: qualunque numero plausibile che non sia una referenza
    loose = [v for v in (_to_float(m.group(0)) for m in re.finditer(_NUM, t))
             if v is not None]
    good = usable(loose)
    if good:
        return max(good), currency

    return None, currency


def _to_float(raw: str) -> Optional[float]:
    s = raw.replace(" ", "").replace("'", "")
    if "," in s and "." in s:
        # l'ultimo separatore è quello decimale
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # virgola decimale solo se seguita da 1-2 cifre finali
        s = s.replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    elif "." in s:
        s = s if re.search(r"\.\d{1,2}$", s) else s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def to_eur(amount: Optional[float], currency: str) -> Optional[float]:
    if amount is None:
        return None
    return round(amount * FX_TO_EUR.get(currency.upper(), 1.0), 2)


# =============================================================================
# REFERENZA
# =============================================================================

def parse_reference(text: str) -> Optional[str]:
    m = REF_RE.search(text or "")
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1)).upper()


_SPLIT_REF = re.compile(r"^(\d{6})([A-Z]{2,6})$")


def matches_reference(listing_ref: Optional[str], text: str, wanted: list[str]) -> bool:
    """Il filtro duro, tollerante su come la referenza è scritta.

    Riconosce tre forme:
      "126710BLRO"            attaccata
      "126710 BLRO"           separata
      "BLRO REF. 126710"      invertita  <- L'Orologiaio di Corte scrive così
    """
    spaced = norm(text).upper()
    hay = re.sub(r"[\s\-_/.]", "", spaced)

    for w in wanted:
        # i punti vanno tolti anche dalla referenza cercata, altrimenti
        # "310.30.42.50.01.002" non trova mai se stessa nel testo normalizzato
        w_clean = re.sub(r"[\s\-_/.]", "", w).upper()
        if listing_ref and re.sub(r"[\s\-_/.]", "", listing_ref).upper() == w_clean:
            return True
        if w_clean in hay:
            return True

        # forma invertita: base e suffisso presenti ma in ordine diverso
        m = _SPLIT_REF.match(w_clean)
        if m:
            base, suffix = m.group(1), m.group(2)
            if base in hay and re.search(rf"(?<![A-Z]){suffix}(?![A-Z])", spaced):
                return True
    return False


# Molti e-commerce infilano referenze popolari nelle descrizioni di prodotti che
# non c'entrano nulla, per posizionarsi su Google. Se ci fidassimo del solo
# testo, un Datejust con "126710BLRO" in fondo alla scheda verrebbe scambiato
# per un Pepsi. Quindi la referenza deve stare nel TITOLO, oppure il titolo deve
# almeno nominare il modello giusto.

# Una referenza Rolex moderna è 6 cifre che iniziano per 1, spesso seguite da
# un suffisso di lettere. Il suffisso NON è un dettaglio: 126710BLRO è il Pepsi,
# 126710BLNR è il Batman. Stesso numero, orologi diversi, prezzi diversi.
_ROLEX_REF_RE = re.compile(r"(?<![0-9A-Z])(1\d{5})\s*-?\s*([A-Z]{2,6})?(?![0-9A-Z])")


_ROLEX_STYLE = re.compile(r"^\d{6}[A-Z]{0,6}$")


def _forma(ref: str) -> str:
    r"""Traduce una referenza nel suo schema: cifre, lettere, separatori.

    "310.30.42.50.01.002" -> \d{3}\.\d{2}\.\d{2}\.\d{2}\.\d{2}\.\d{3}
    "126710BLRO"          -> \d{6}[A-Z]{4}

    Serve a riconoscere un'altra referenza *dello stesso marchio*: una stringa
    con la stessa forma ma valore diverso è un altro orologio, qualunque sia
    la casa che l'ha numerata.
    """
    pezzi, i = [], 0
    for m in re.finditer(r"\d+|[A-Z]+|[.\-/]", ref.upper()):
        t = m.group(0)
        if t.isdigit():
            pezzi.append(rf"\d{{{len(t)}}}")
        elif t.isalpha():
            pezzi.append(rf"[A-Z]{{{len(t)}}}")
        else:
            pezzi.append(re.escape(t))
    return "".join(pezzi)


def altra_referenza_stessa_forma(title: str, wanted: list[str]) -> bool:
    """True se il titolo contiene una referenza della stessa forma ma diversa.

    Caso reale: cercando lo Speedmaster 310.30.42.50.01.002 è arrivato un
    310.30.42.50.04.001 — il Moonwatch bianco, altro orologio, altro prezzo.
    Era passato perché la referenza giusta compariva fra i prodotti correlati
    della sua scheda.
    """
    if not title or not wanted:
        return False
    hay = norm(title).upper()
    attese = {re.sub(r"[\s\-_/.]", "", w).upper() for w in wanted}

    for w in wanted:
        forma = _forma(w)
        if len(forma) < 8:          # schemi troppo corti: troppi falsi positivi
            continue
        for m in re.finditer(rf"(?<![0-9A-Z]){forma}(?![0-9A-Z])", hay):
            trovata = re.sub(r"[\s\-_/.]", "", m.group(0))
            if trovata not in attese:
                return True
    return False


def other_reference_in_title(title: str, wanted: list[str]) -> bool:
    """True se il titolo nomina una referenza diversa da quelle cercate."""
    if altra_referenza_stessa_forma(title, wanted):
        return True

    # controllo aggiuntivo sulla numerazione Rolex: sei cifre nude, dove la
    # forma non basta a distinguere un 126234 da un 126710
    clean = [re.sub(r"[\s\-_/]", "", w).upper() for w in wanted]
    if not clean or not all(_ROLEX_STYLE.match(w) for w in clean):
        return False

    hay = norm(title).upper()
    wanted_full = set(clean)
    wanted_bases = {w[:6] for w in wanted_full}

    for m in _ROLEX_REF_RE.finditer(hay):
        base, suffix = m.group(1), (m.group(2) or "")
        if suffix:
            # referenza completa: il confronto deve includere il suffisso
            if base + suffix not in wanted_full:
                return True
        elif base not in wanted_bases:
            return True
    return False


def is_target_watch(title: str, text: str, wanted: list[str],
                    model_keywords: list[str] | None = None,
                    exclude_keywords: list[str] | None = None) -> bool:
    """Il vero filtro duro, resistente allo spam SEO.

    Ordine dei controlli:
      1. il titolo nomina un modello escluso → non è lui
      2. referenza nel titolo                → è lui, punto
      3. titolo troppo povero per decidere   → ci si affida al corpo
      4. titolo nomina un'altra referenza    → non è lui
      5. referenza nel corpo + modello nel titolo → è lui
    """
    title = title or ""
    model_keywords = model_keywords or []
    nt_early = norm(title)

    # Batman, Sprite, Root Beer: stesso modello, orologio diverso.
    for k in (exclude_keywords or []):
        if norm(k) in nt_early:
            return False

    if matches_reference(None, title, wanted):
        return True

    if not matches_reference(None, text, wanted):
        return False

    nt = norm(title)
    if len(nt) < 12:
        return True

    if other_reference_in_title(title, wanted):
        return False

    return any(norm(k) in nt for k in model_keywords)


def matches_by_name(title: str, text: str, brand: str | None,
                    must_include: list[str],
                    exclude_keywords: list[str] | None = None) -> bool:
    """Riconosce un orologio dal nome invece che dalla referenza.

    Serve per i modelli che i venditori non identificano quasi mai con la
    referenza completa: uno Zenith Elite viene messo in vendita come "Zenith
    Elite Classic Automatic Ultra Thin", e cercare `18.2010.681/01.C498` in
    quel titolo non trova niente.

    La regola e' volutamente severa: *tutte* le parole di `must_include`
    devono comparire, non una qualsiasi. E' quello che distingue un "Elite
    Ultra Thin" da un "Elite Chronomaster", che condividono la prima parola.
    """
    nt = norm(title or "")
    for k in (exclude_keywords or []):
        if norm(k) in nt:
            return False
    tutto = norm(f"{title or ''} {text or ''}")
    if brand and norm(brand) not in tutto:
        return False
    # Sul titolo se e' abbastanza descrittivo, altrimenti sul corpo: alcune
    # fonti mettono nel link solo "Zenith" e il resto nella scheda.
    campo = nt if len(nt) >= 12 else tutto
    return bool(must_include) and all(norm(k) in campo for k in must_include)


# =============================================================================
# DISPONIBILITA
# =============================================================================

_SOLD_RE = re.compile(
    r"prodotto non disponibile|non (piu )?disponibile|\bvenduto\b|\bvendute?\b|"
    r"sold\s*out|out of stock|esaurito|non disponibile al momento|"
    r"articolo non disponibile",
    re.I,
)


def parse_sold(text: str) -> bool:
    """True se l'annuncio è chiaramente già venduto o non acquistabile."""
    return bool(_SOLD_RE.search(norm(text or "")))


# =============================================================================
# ANNO
# =============================================================================

_YEAR_CONTEXT = re.compile(
    r"(?:anno|year|jahr|annee|del|of|dated|produzione|prod\.?|circa|ca\.?|"
    r"garanzia|warranty|card|papers|cartellino|box\s*&?\s*papers|full\s*set)"
    r"[^\d]{0,25}(19[89]\d|20[0-4]\d)"
    r"|(19[89]\d|20[0-4]\d)\s*(?:anno|year|model|modello)",
    re.I,
)


def parse_year(text: str) -> Optional[int]:
    """Preferisce anni con contesto esplicito; altrimenti anni plausibili isolati."""
    if not text:
        return None
    t = norm(text)

    # evita di leggere l'anno dentro numeri lunghi (referenze, seriali)
    t_masked = re.sub(r"\d{5,}", " ", t)

    for m in _YEAR_CONTEXT.finditer(t_masked):
        y = m.group(1) or m.group(2)
        if y:
            yi = int(y)
            if 1980 <= yi <= _CURRENT_YEAR + 1:
                return yi

    years = [int(y) for y in re.findall(r"\b(19[89]\d|20[0-4]\d)\b", t_masked)]
    years = [y for y in years if 1980 <= y <= _CURRENT_YEAR + 1]
    if years:
        return max(years)          # in un annuncio l'anno più recente è quasi sempre quello giusto
    return None


# =============================================================================
# BRACCIALE / CONDIZIONE / CORREDO
# =============================================================================

def parse_bracelet(text: str) -> Optional[str]:
    t = norm(text)
    if re.search(r"\bjubil(e|e?e|ee|aum|é)\b|\bgiubileo\b", t):
        return "jubilee"
    if re.search(r"\boyster\s*(bracelet|bracciale)?\b", t) and "oysterflex" not in t:
        # "Oyster Perpetual" non indica il bracciale
        if not re.search(r"oyster\s+perpetual", t):
            return "oyster"
    return None


# L'ordine conta: le espressioni più specifiche vanno prima di quelle generiche,
# altrimenti "come nuovo" verrebbe letto come "new".
_CONDITION_PATTERNS = [
    ("unworn", r"\bunworn\b|mai indossat|nuovo mai indossat|new old stock|\bnos\b|stickered|con adesiv"),
    ("like_new", r"like new|quasi nuovo|near mint"),
    ("mint", r"\bmint\b|pari al nuovo|come nuovo|as new"),
    ("new", r"\bnew\b|\bnuov[oa]\b|\bneu\b|brand new"),
    ("excellent", r"\bexcellent\b|\beccellent|ottime condizioni|ottimo stato"),
    ("very_good", r"very good|molto buon|buone condizioni"),
    ("good", r"\bgood\b|\bbuono\b|discrete condizioni|segni d'uso|worn"),
]


def parse_condition(text: str) -> Optional[str]:
    t = norm(text)
    for label, pattern in _CONDITION_PATTERNS:
        if re.search(pattern, t):
            return label
    return None


def parse_full_set(text: str) -> Optional[bool]:
    t = norm(text)
    if re.search(r"full\s*set|complete\s*set|scatola e (garanzia|documenti)|"
                 r"box\s*(and|&|\+)\s*papers|corredo completo|completo di tutto|"
                 r"con scatola e garanzia|b\s*&\s*p\b", t):
        return True
    if re.search(r"solo orologio|watch only|senza scatola|no box|no papers|"
                 r"senza garanzia|head only|nudo", t):
        return False
    return None


def parse_never_polished(text: str) -> Optional[bool]:
    t = norm(text)
    if re.search(r"mai lucidat|never polished|unpolished|non lucidat|"
                 r"smussi (intatti|integri)|spigoli vivi", t):
        return True
    if re.search(r"\blucidat[oa]\b|\bpolished\b|lucidatura", t):
        return False
    return None


# =============================================================================
# GARANZIA / PAESE
# =============================================================================

_REGION_PATTERNS = [
    # San Marino prima dell'Italia: "San Marino" contiene un riferimento
    # geografico italiano e verrebbe assorbito dal pattern successivo.
    ("SM", r"san\s?marino|\brsm\b|repubblica di san marino"),
    ("IT", r"\bitali|\bitaly\b|\bit\b\s*(warranty|garanzia)|garanzia italian"),
    ("AE", r"\buae\b|emirat|dubai|abu dhabi|\bae\b\s*warranty"),
    ("SA", r"saudi|arabia saudita|\bksa\b|riyadh|jeddah"),
    ("QA", r"\bqatar\b|doha"),
    ("KW", r"\bkuwait\b"),
    ("BH", r"\bbahrain\b"),
    ("OM", r"\boman\b|muscat"),
    ("TR", r"\bturkey\b|turchia|\bturkish\b|istanbul"),
    ("HK", r"hong ?kong\b"),
    ("JP", r"\bjapan\b|giappone|\bjapanese\b|tokyo"),
    ("CH", r"\bswitzerland\b|svizzera|\bswiss\b(?!\s*made)|geneva|ginevra|zurich"),
    ("UK", r"\buk\b|united kingdom|\bengland\b|regno unito|london"),
    ("US", r"\busa\b|united states|\bu\.s\.\b|stati uniti"),
    ("DE", r"germania|\bgermany\b|deutschland"),
    ("FR", r"\bfrance\b|francia"),
    ("ES", r"\bspain\b|spagna|espana"),
    ("NL", r"netherlands|olanda|paesi bassi|amsterdam"),
    ("EU", r"\beurop|\beu\b\s*(warranty|garanzia)|\bue\b\s*garanzia"),
]

_EU_MEMBERS = {"IT", "DE", "FR", "ES", "NL", "EU", "AT", "BE", "PT", "IE"}

# San Marino non è nell'Unione: resta una sigla a sé, sia per la garanzia sia
# per la posizione del venditore.
_NON_EU_VICINI = {"SM", "CH", "VA"}


def parse_warranty_region(text: str) -> Optional[str]:
    """Cerca prima nel contesto 'garanzia/warranty', poi in tutto il testo."""
    t = norm(text)

    window = None
    m = re.search(r"(garanzia|warranty|card|certificato)(.{0,80})", t, re.S)
    if m:
        window = m.group(2)

    for haystack in (window, t):
        if not haystack:
            continue
        for code, pattern in _REGION_PATTERNS:
            if re.search(pattern, haystack):
                return code
    return None


def normalize_region_group(code: Optional[str]) -> Optional[str]:
    if code is None:
        return None
    if code in _NON_EU_VICINI:
        return code
    return "EU" if code in _EU_MEMBERS and code != "IT" else code


# =============================================================================
# POSIZIONE DEL VENDITORE
# =============================================================================

# Dove si trova l'orologio è cosa diversa da dove è stata emessa la garanzia.
# Chrono24 le espone come due campi distinti, e confonderle porta a valutare
# male un orologio a Milano con garanzia emiratina — o viceversa.
_LOCATION_CONTEXT = re.compile(
    r"(?:luogo|location|standort|paese|country|si trova|ubicazion|sede|"
    r"spedizione da|ships? from|venditore in)\s*:?\s*(.{0,50})",
    re.I | re.S,
)


def parse_location(text: str) -> Optional[str]:
    """Paese in cui si trova l'orologio, non quello della garanzia.

    Cerca solo dentro un campo esplicito: dedurlo dal testo intero
    significherebbe leggere la garanzia e chiamarla posizione.
    """
    if not text:
        return None
    t = norm(text)
    for m in _LOCATION_CONTEXT.finditer(t):
        finestra = m.group(1)
        for code, pattern in _REGION_PATTERNS:
            if re.search(pattern, finestra):
                return code
    return _paese_chrono24(text)


# Chrono24 chiude il titolo di ogni annuncio con la sigla del paese del
# venditore, dopo la riga delle spese di spedizione:
#     "... 20.999 EUR + 59 EUR di spese di spedizione FR"
#     "... 23.495 EUR Spedizione gratuita JP"
# Niente re.IGNORECASE: la sigla del paese e' maiuscola per definizione, ed
# e' l'unica cosa che la distingue dall'ultima parola della frase.
_PAESE_IN_CODA = re.compile(
    r"(?:[Ss]pedizion|[Ss]hipping|[Vv]ersand)[^\n]{0,40}?\b([A-Z]{2})\s*$")


def _paese_chrono24(text: str) -> Optional[str]:
    """La sigla in fondo al titolo di un annuncio Chrono24.

    Senza questa lettura ogni annuncio del canale principale risultava di
    provenienza ignota, e la regola "ignoto = trattalo come europeo" faceva
    passare per vicini venditori giapponesi o americani — orologi che non
    andresti mai a vedere di persona.
    """
    m = _PAESE_IN_CODA.search((text or "").strip())
    if not m:
        return None
    sigla = m.group(1).upper()
    return sigla if sigla.isalpha() and sigla not in {"EU", "OR", "DI", "DA"} else None


# =============================================================================
# SERIALE
# =============================================================================

def parse_serial(text: str) -> Optional[str]:
    """Seriali Rolex moderni: 8 caratteri alfanumerici, spesso mascherati."""
    m = re.search(r"\b(?:serial|seriale|s/n|sn)[^\w]{0,6}([A-Z0-9]{4,8}[X*]{0,4})\b",
                  text or "", re.I)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b([0-9A-Z]{2}[0-9A-Z]{6})\b(?=.{0,30}(serial|seriale))", text or "", re.I)
    return m.group(1).upper() if m else None


# =============================================================================
# ORCHESTRAZIONE
# =============================================================================

def enrich(listing, source_cfg: dict | None = None):
    """Popola tutti i campi derivabili dal testo dell'annuncio."""
    text = f"{listing.title}\n{listing.raw_text}"
    source_cfg = source_cfg or {}

    if listing.price_eur is None:
        # il campo prezzo dedicato è molto più affidabile del testo intero
        amount, currency = parse_price(listing.raw_price or "")
        if amount is None:
            amount, currency = parse_price(text)
        listing.price_original = amount
        listing.currency = currency
        listing.price_eur = to_eur(amount, currency)

    listing.reference = listing.reference or parse_reference(text)
    listing.year = listing.year or parse_year(text)
    listing.serial = listing.serial or parse_serial(text)
    listing.bracelet = listing.bracelet or parse_bracelet(text)
    listing.condition = listing.condition or parse_condition(text)
    if listing.full_set is None:
        listing.full_set = parse_full_set(text)
    if listing.never_polished is None:
        listing.never_polished = parse_never_polished(text)
    if listing.warranty_region is None:
        listing.warranty_region = normalize_region_group(parse_warranty_region(text))
    if listing.seller_country is None:
        listing.seller_country = normalize_region_group(parse_location(text))
    if listing.sold is None:
        listing.sold = parse_sold(text)

    if listing.seller_trust == 0:
        listing.seller_trust = int(source_cfg.get("seller_trust", 0))
    if listing.is_dealer is None:
        listing.is_dealer = source_cfg.get("dealer")
    if listing.seller_country is None:
        listing.seller_country = source_cfg.get("country")

    return listing


# Pagine di servizio dei marketplace: sembrano link normali, stanno in mezzo
# agli annunci, ma non portano a nessun orologio.
_PAGINA_DI_SERVIZIO = re.compile(
    r"searchtask|saved-?search|/user/|/utente/|/myaccount|/notification|"
    r"/impostazioni|/settings",
    re.I,
)


def e_pagina_di_servizio(url: str) -> bool:
    """Vero se l'URL e' un bottone del messaggio, non un annuncio.

    Guarda solo il percorso, mai la query: i link Chrono24 si portano dietro
    `ikcampaign=feat-SavedSearch` nella coda di tracciamento, e cercare
    "savedsearch" nell'URL intero cancellava venticinque annunci veri.
    """
    return bool(_PAGINA_DI_SERVIZIO.search((url or "").split("?")[0]))


# Estensioni di file: un link a un'immagine non e' un annuncio, e' la foto
# ingrandita della galleria.
_ASSET = re.compile(r"\.(?:jpe?g|png|webp|gif|svg|avif|bmp|tiff?|pdf|zip|"
                    r"mp4|webm|mov|css|js|ico)$", re.I)

# Pagine di elenco: categorie, vetrine, carrello. Contengono molti orologi e
# nessuno in particolare, quindi qualsiasi prezzo ci si legga appartiene a
# un altro annuncio.
_PAGINA_DI_ELENCO = re.compile(
    r"^/(?:categoria|category|collezion\w*|collections?|shop|negozio|store|"
    r"catalogo|catalog|brands?|marche?|tag|page|blog|news)(?:/|$)|"
    r"/vendita-|/orologi-usati|/chi-siamo|/contatt|/carrello|/cart(?:/|$)|"
    r"/checkout|/wishlist|/il-negozio",
    re.I,
)


def e_url_di_annuncio(url: str) -> bool:
    """Questo indirizzo punta a un singolo orologio in vendita?

    Serve una regola esplicita perche' i due modi di sbagliare sono entrambi
    silenziosi e producono la stessa cosa: un annuncio inventato, con il
    prezzo rubato a qualcun altro.

      - il link alla foto ingrandita (`.../IMG_5010.webp`)
      - il link alla vetrina (`.../vendita-orologi-di-lusso-bologna/`)

    Guarda solo il percorso: nella query finiscono parametri di tracciamento
    che contengono di tutto, e cercarci parole chiave ha gia' cancellato per
    sbaglio venticinque annunci veri.
    """
    from urllib.parse import urlparse
    if not url or e_pagina_di_servizio(url):
        return False
    percorso = urlparse(url).path or "/"
    if _ASSET.search(percorso):
        return False
    return not _PAGINA_DI_ELENCO.search(percorso)


# Marche note: servono solo a riconoscere che un titolo parla di un ALTRO
# orologio. Non e' un elenco di cose monitorate, e' un elenco di cose che,
# se compaiono nel titolo al posto della marca giusta, dicono "non e' lui".
_MARCHE = [
    "rolex", "omega", "tudor", "tag heuer", "heuer", "zenith",
    "vacheron constantin", "audemars piguet", "patek philippe", "iwc",
    "panerai", "breitling", "cartier", "jaeger", "hublot", "chopard",
    "blancpain", "grand seiko", "seiko", "longines", "baume", "bell & ross",
    "montblanc", "oris", "tissot", "glashutte", "nomos", "bulgari", "piaget",
    "girard", "ulysse nardin", "franck muller", "richard mille", "chanel",
]


def altra_marca_nel_titolo(title: str, brand: str | None) -> Optional[str]:
    """Il titolo nomina una marca diversa da quella cercata?

    Guardia trasversale: qualunque cosa sia andata storta a monte — un blocco
    che ha invaso l'annuncio accanto, una pagina di risultati che riecheggia
    la tua ricerca — se il titolo dice "Rolex GMT-Master" e stiamo cercando un
    Omega, non e' lui. Costa poco e taglia una classe intera di errori.
    """
    if not brand or not title:
        return None
    nt = norm(title)
    nb = norm(brand)
    if nb in nt:
        return None
    for m in _MARCHE:
        if norm(m) in nb:          # la marca cercata, scritta in altro modo
            continue
        if norm(m) in nt:
            return m
    return None


# Le pagine di ricerca dei negozi riecheggiano quello che hai cercato:
# "Risultati di ricerca per 15450ST". Quell'eco basta a far sembrare che la
# referenza sia presente, anche quando la pagina non contiene quell'orologio.
_ECO_RICERCA = re.compile(
    r"risultati?\s+di\s+ricerca|search\s+results?|hai\s+cercato|"
    r"nessun\s+risultato|no\s+results?\s+found|suchergebnis",
    re.I,
)


def togli_eco_ricerca(soup) -> None:
    """Cancella dal documento il testo che ripete la ricerca fatta.

    Modifica il documento in luogo, prima che qualcuno ci cerchi dentro una
    referenza. Senza questo, cercare "15450ST" su un negozio che non ha
    nessun Royal Oak restituiva comunque una corrispondenza: quella con la
    tua stessa domanda.
    """
    for tag in list(soup.find_all(["h1", "h2", "h3", "p", "span", "div", "title"])):
        testo = tag.get_text(" ", strip=True)
        if len(testo) <= 120 and _ECO_RICERCA.search(testo):
            tag.decompose()


def referenza_esclusa(title: str, text: str, cercate: list[str],
                      escluse: list[str]) -> Optional[str]:
    """Una variante che NON vuoi, nominata esplicitamente.

    La logica e' a tre stati, non a due, e l'ordine conta:

      - c'e' la referenza che cerchi        -> e' lui, punto
      - non c'e' nessuna delle due          -> ambiguo, lo tieni comunque
      - c'e' solo quella esclusa            -> non e' lui

    Il caso ambiguo va tenuto: mezzo mercato scrive "Speedmaster Moonwatch
    42mm" senza suffisso, e scartarli tutti significherebbe perdere piu'
    annunci buoni di quanti se ne evitino di sbagliati. Lo scarto scatta solo
    quando il venditore ha detto chiaramente che e' l'altro.
    """
    if not escluse:
        return None
    tutto = f"{title or ''} {text or ''}"
    if matches_reference(None, tutto, cercate):
        return None
    for r in escluse:
        if matches_reference(None, tutto, [r]):
            return r
    return None
