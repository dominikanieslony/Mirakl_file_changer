"""
Silnik regul poprawiajacych dane produktowe.

WAZNE: automatyczne tlumaczenie angielskich slow na niemieckie odbywa sie w:
  - tytule (wszystkie skladowe OPROCZ dokladnego fragmentu bedacego nazwa
    modelu - modelName_text - ktory nigdy nie jest tlumaczony/modyfikowany),
  - Long Description (LongDescription_de/Langbeschreibung),
  - materialComposition_de/Materialzusammensetzung (osobne pole),
  - color_manufacturer_text/Herstellerfarbbezeichnung (osobne pole).
Pozostale pola (np. genders/ages/colors) sa jedynie flagowane do recznej
weryfikacji wzgledem zamknietych list kodow, bez automatycznej zmiany wartosci.

Kazda funkcja fix_* zwraca (nowa_wartosc, zmieniono: bool, opis: str|None).
process_product() spina wszystko w jeden przebieg per produkt i zwraca:
  - poprawiony wiersz (OrderedDict)
  - liste problemow: dict(attribute, error_code, message, severity)
    severity in {"auto_fixed", "manual_review"}
"""
from __future__ import annotations

import re
from collections import OrderedDict

from . import dictionaries as D
from .categories import CategoryTree
from .categories import _normalize as _cat_normalize
from .categories import _stem as _cat_stem

TITLE_CODE_CANDIDATES = ["ShortDescription_de", "Produktname"]
DESC_CODE_CANDIDATES = ["LongDescription_de", "Langbeschreibung"]
CATEGORY_CODE_CANDIDATES = ["CATEGORY", "Kategorie"]
COLOR_MANUFACTURER_CANDIDATES = ["color_manufacturer_text", "Herstellerfarbbezeichnung"]
MATERIAL_CODE_CANDIDATES = ["materialComposition_de", "Materialzusammensetzung"]
GENDER_CODE_CANDIDATES = ["genders", "Geschlecht"]
AGE_CODE_CANDIDATES = ["ages", "Altersgruppe"]
COLORS_FIELD_CANDIDATES = ["colors", "Limango Farbe"]

# slowa plci/demografii, ktore nie powinny wystepowac w tytule (docelowy format
# "Typ produktu + Model + in Kolor" - zalozenie 1d w README.md; plec produktu
# jest juz osobno w polu `genders`, ktorego walidacja NIE zmienia sie przez to).
TITLE_GENDER_WORDS = {
    "mädchen", "jungen", "junge", "baby", "damen", "herren", "kinder", "unisex",
}

# kody rozmiarow, ktore nie powinny wystepowac w tytule (rozmiar to osobna
# koncepcja niz "Typ produktu + Model + in Kolor" - potwierdzone przez
# uzytkownika: "wszystkie rozmiary [...] powinny byc zawsze usuwane z tytulu").
# Dopasowywane jako cale slowo, wielkosc liter ma znaczenie (male "s"/"m"/"l"
# to zwykle zwykle slowa, nie kody rozmiaru).
TITLE_SIZE_TOKENS = {"XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "2XL", "3XL"}

VARIANT_GROUP_CANDIDATES = ["variant_group_code", "Variant Group"]

# separator miedzy ogolnym okresleniem koloru a motywem w color_manufacturer_text
# (zaobserwowane: "Mehrfarbig - Tropischer Dschungel"). Celowo bez "/" -
# "Rot/Blau" to dwa kolory, nie prefiks + motyw.
_COLOR_PREFIX_SEPARATOR_RE = re.compile(r"\s+[-–—]\s+|\s*:\s+")

# ogolne okreslenia "wielokolorowy" - fallback dla produktow bez grupy
# wariantow (patrz variant_group_color_prefixes)
MULTICOLOR_WORDS = {"mehrfarbig", "bunt", "farbig", "multicolor", "multicolored",
                    "multi-colored", "colorful"}

# stare wymiary na koncu tytulu (np. "- 9,8 x 14,5 cm") - zastepowane
# sufiksem z build_dimension_suffix, gdy pola wymiarow sa kompletne
_OLD_DIMENSIONS_RE = re.compile(
    r"\s*[-–—]?\s*\d+(?:[.,]\d+)?\s*[x×]\s*\d+(?:[.,]\d+)?"
    r"(?:\s*[x×]\s*\d+(?:[.,]\d+)?)?\s*(?:mm|cm|m)\s*$",
    re.IGNORECASE,
)


def _first_present(row: dict, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in row:
            return c
    return None


def issue(attribute, error_code, message, severity):
    return {
        "attribute": attribute,
        "error_code": error_code,
        "message": message,
        "severity": severity,
    }


# --- pojedyncze naprawy pol -----------------------------------------------------

def fix_broken_umlauts(text: str) -> tuple[str, bool]:
    if not text:
        return text, False
    changed = False
    new_text = text
    for broken, fixed in D.BROKEN_UMLAUT_WORDS.items():
        if broken == "Weiss":  # to nie jest blad, tylko wariant pisowni - pomijamy
            continue
        pattern = re.compile(rf"\b{re.escape(broken)}\b")
        if pattern.search(new_text):
            new_text = pattern.sub(fixed, new_text)
            changed = True
    return new_text, changed


def fix_double_spaces_and_grammar(text: str) -> tuple[str, bool]:
    if not text:
        return text, False
    changed = False
    new_text = text
    if "  " in new_text:
        new_text = re.sub(r"\s{2,}", " ", new_text).strip()
        changed = True
    # " im " przed kolorem powinno byc " in " zgodnie ze wzorcem "Typ (+Model) in Farbe".
    # Gdy tytul ma juz 'in' (np. "im Querformat in Oldtimer"), 'im' jest
    # zwyklym przyimkiem, nie wstepem do koloru - zostawiamy.
    if re.search(r"\bin\b", new_text):
        return new_text, changed
    fixed = re.sub(r"\bim\b(?=\s+[A-ZÄÖÜ])", "in", new_text)
    if fixed != new_text:
        new_text = fixed
        changed = True
    return new_text, changed


def normalize_gender(value: str) -> tuple[str, bool, str | None]:
    """Poprawna wartosc pola to CODE: female/male/unisex (angielski - code i label
    sa tu identyczne). Zgodnie z ustaleniem o ograniczonym zakresie auto-tlumaczenia,
    wartosc NIE jest tu zmieniana automatycznie - tylko flagowana z podpowiedzia
    poprawnego kodu."""
    if not value:
        return value, False, None
    v = value.strip()
    if v in D.GENDERS_VALID_CODES:
        return v, False, None
    suggestion = D.GENDERS_LABEL_TO_CODE.get(v.lower())
    if suggestion:
        return v, False, (f"Wartosc '{v}' nie jest poprawnym kodem pola - "
                           f"powinno byc: '{suggestion}'.")
    return v, False, (f"Nieznana wartosc '{v}' dla pola plci - oczekiwany kod to jeden z: "
                       f"{sorted(D.GENDERS_VALID_CODES)}.")


def normalize_age(value: str) -> tuple[str, bool, str | None]:
    """Poprawna wartosc pola to CODE: baby/child/adult (angielski, l. pojedyncza).
    Zgodnie z ustaleniem o ograniczonym zakresie auto-tlumaczenia, wartosc NIE
    jest tu zmieniana automatycznie - tylko flagowana z podpowiedzia poprawnego
    kodu."""
    if not value:
        return value, False, None
    v = value.strip()
    if v in D.AGES_VALID_CODES:
        return v, False, None
    suggestion = D.AGES_LABEL_TO_CODE.get(v.lower())
    if suggestion:
        return v, False, (f"Wartosc '{v}' to etykieta (label), nie poprawny kod (code) pola - "
                           f"powinno byc: '{suggestion}'.")
    return v, False, (f"Nieznana wartosc '{v}' dla grupy wiekowej - oczekiwany kod to jeden z: "
                       f"{sorted(D.AGES_VALID_CODES)}.")


def normalize_colors_field(value: str) -> tuple[str, bool, str | None]:
    """Pole 'colors'/'Limango Farbe' - zamkniety slownik kodow (patrz
    COLORS_VALID_CODES). Wartosc moze byc wielokrotna, rozdzielona '|'
    (zaobserwowane w good_data_1.xml). Tylko flagowanie, bez auto-zmiany,
    zgodnie z ustalonym zakresem auto-tlumaczenia."""
    if not value:
        return value, False, None
    tokens = [t.strip() for t in value.split("|") if t.strip()]
    problems = []
    for t in tokens:
        if t.lower() in D.COLORS_VALID_CODES:
            continue
        suggestion = D.COLORS_LABEL_TO_CODE.get(t.lower())
        if suggestion:
            problems.append(f"'{t}' -> powinno byc '{suggestion}'")
        else:
            problems.append(f"'{t}' - nieznany kod koloru")
    if problems:
        return value, False, "Pole colors: " + "; ".join(problems) + "."
    return value, False, None


def apply_translation_fixes(text: str) -> tuple[str, bool]:
    """Wspolny zestaw poprawek jezykowych (EN->DE slowa koloru/materialu +
    naprawa ucietych znakow specjalnych) - uzywany w tytule i w Long Description."""
    if not text:
        return text, False
    changed_any = False
    new_text, ch = fix_broken_umlauts(text)
    changed_any = changed_any or ch
    new_text, ch, _ = fix_color_word(new_text)
    changed_any = changed_any or ch
    for en, de in D.MATERIALS_EN_TO_DE.items():
        if en == de:
            continue
        pattern = re.compile(rf"\b{re.escape(en)}\b")
        if pattern.search(new_text):
            new_text = pattern.sub(de, new_text)
            changed_any = True
    return new_text, changed_any


def extract_color_from_description(desc: str) -> str | None:
    """Fallback dla brakujacego color_manufacturer_text: niektore pliki nie
    maja tego pola w ogole (zaobserwowane), ale Long Description zawiera
    jawnie oznaczona wzmianke 'Farbe: X' - to jedyne bezpieczne (nie
    zgadywane) zrodlo koloru z wolnego tekstu, bo jest jednoznacznie
    oznaczone etykieta, w przeciwienstwie do prob rozpoznawania
    dowolnych slow-kolorow w tytule (brak niezawodnego slownika do tego)."""
    if not desc:
        return None
    m = re.search(r"Farbe\s*:\s*([^.<\n]+)", desc, re.IGNORECASE)
    if not m:
        return None
    color = re.sub(r"\s{2,}", " ", m.group(1)).strip()
    if not color:
        return None
    return _format_extracted_color(color)


def _format_extracted_color(color: str) -> str:
    """Formatuje kolor wyekstrahowany z opisu (patrz extract_color_from_description):
    kazde slowo-kolor z wielkiej litery, a przy wielu kolorach oddzielone '/'
    (potwierdzone przez uzytkownika, np. 'Rot/Gelb') - niezaleznie od tego, czy
    w oryginalnym opisie byly rozdzielone spacja, '/', przecinkiem czy 'und'.
    Dotyczy WYLACZNIE tego fallbacku - color_manufacturer_text jest zawsze
    uzywane bez zmian, verbatim."""
    parts = re.split(r"[/,&]+|\s+und\s+|\s+", color, flags=re.IGNORECASE)
    parts = [p[:1].upper() + p[1:] for p in (p.strip() for p in parts) if p]
    return "/".join(parts)


def _strip_bare_color_word_fragments(text: str, color_value: str) -> tuple[str, bool]:
    """Gdy kolor pochodzi z fallbacku (wyekstrahowany z opisu - patrz
    extract_color_from_description), moze on juz wystepowac w tytule w innej
    formie interpunkcyjnej (np. tytul ma 'hellblau weiss', a z opisu
    wyekstrahowano 'hellblau/weiss') - dokladne dopasowanie calej frazy w
    ensure_in_before_color by tego nie wykrylo, co prowadziloby do
    zdublowania koloru. Usuwamy pojedyncze slowa-skladowe koloru (>=3 znaki,
    zeby pominac szum typu spojniki) wystepujace w tytule jako cale slowa."""
    if not text or not color_value:
        return text, False
    words = [w for w in re.split(r"[^A-Za-zÀ-ÖØ-öø-ÿ]+", color_value) if len(w) >= 3]
    if not words:
        return text, False
    new_text = text
    changed = False
    for w in words:
        pattern = re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE)
        if pattern.search(new_text):
            new_text = pattern.sub("", new_text)
            changed = True
    if not changed:
        return text, False
    new_text = re.sub(r"^[\s\-:,/]+", "", new_text)
    new_text = re.sub(r"[\s\-:,/]+$", "", new_text)
    new_text = re.sub(r"\s{2,}", " ", new_text).strip()
    return new_text, new_text != text


def ensure_in_before_color(title: str, color_value: str) -> tuple[str, bool]:
    """Wymusza wzorzec tytulu '... in {color_manufacturer_text}' (color_manufacturer_text
    jest zrodlem prawdy dla koloru w tytule - zawsze, bez wyjatkow, potwierdzone
    przez uzytkownika). Zaklada, ze tytul zostal juz oczyszczony z tresci, ktora
    duplikowalaby kolor (patrz strip_title_junk() - wywolywane PRZED ta funkcja
    w process_product()). Kolejnosc sprawdzania (pierwsze dopasowanie wygrywa):
    1. kolor juz poprawnie poprzedzony 'in'/'im' - bez zmian,
    2. kolor WYSTEPUJE w tytule, ale bez poprawnego przedrostka (przecinek,
       myslnik, nic) - wstaw ' in ' bezposrednio przed nim,
    3. tytul ma 'in X'/'im X' z INNYM kolorem X (kolor_value nie wystepuje
       nigdzie w tytule) - podmien X na color_value,
    4. kolor nigdzie nie wystepuje i nie ma zadnego 'in X' - dopisz na koncu."""
    if not title or not color_value:
        return title, False

    # 1) kolor juz poprawny
    exact = re.compile(r"\b(in|im)\s+" + re.escape(color_value) + r"\b", re.IGNORECASE)
    if exact.search(title):
        return title, False

    # 2) kolor wystepuje w tytule (dokladne slowo), ale bez poprawnego 'in'/'im'
    # przed nim - usuwamy to wystapienie (wraz z sasiadujaca interpunkcja) i
    # dopisujemy 'in {kolor}' na koncu, zeby zachowac docelowy format "Typ
    # (+Model) in Kolor" (kolor ZAWSZE na koncu - jesli tylko wstawimy 'in'
    # w miejscu, gdzie kolor akurat sie znajduje, a jest to np. pierwsze
    # slowo tytulu, powstaje bezsensowne 'in Kolor ...' na poczatku).
    present = re.compile(r"[,\-–—]?\s*" + re.escape(color_value) + r"\b", re.IGNORECASE)
    m = present.search(title)
    if m:
        remainder = title[:m.start()] + title[m.end():]
        remainder = re.sub(r"^[\s\-:,]+", "", remainder)
        remainder = re.sub(r"[\s\-:,]+$", "", remainder)
        remainder = re.sub(r"\s{2,}", " ", remainder).strip()
        new_title = f"{remainder} in {color_value}" if remainder else f"in {color_value}"
        return new_title, new_title != title

    # 3) tytul ma 'in X'/'im X' z innym kolorem - podmien X na color_value
    other = re.compile(r"\b(in|im)\s+([^,;()–—]+?)(?=\s*(?:[,;()–—]|$))", re.IGNORECASE)
    m = other.search(title)
    if m:
        new_title = title[:m.start()] + "in " + color_value + title[m.end():]
        new_title = re.sub(r"\s{2,}", " ", new_title).strip()
        return new_title, new_title != title

    # 4) kolor w ogole nie wystepuje w tytule - dopisz na koncu
    new_title = f"{title.rstrip()} in {color_value}"
    return new_title, True


def _strip_gender_words(text: str) -> tuple[str, bool]:
    """Usuwa z tekstu slowa plci/demografii (TITLE_GENDER_WORDS), gdziekolwiek
    wystepuja jako cale slowo - obsluguje zarowno forme ze spacja
    ("Baby Madchen X" -> "X"), jak i z lacznikiem ("Madchen-Set" -> "Set")."""
    if not text:
        return text, False
    new_text = text
    changed = False
    for word in TITLE_GENDER_WORDS:
        pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
        if pattern.search(new_text):
            new_text = pattern.sub("", new_text)
            changed = True
    if not changed:
        return text, False
    # sprzataj slady po usunieciu (osierocone laczniki/dwukropki/przecinki na
    # brzegach, nadmiarowe spacje)
    new_text = re.sub(r"^[\s\-:,]+", "", new_text)
    new_text = re.sub(r"[\s\-:,]+$", "", new_text)
    new_text = re.sub(r"\s{2,}", " ", new_text).strip()
    return new_text, new_text != text


def _strip_size_tokens(text: str) -> tuple[str, bool]:
    """Usuwa z tytulu wzmianki o rozmiarze - literowe kody (TITLE_SIZE_TOKENS:
    S/M/L/XL/...) oraz zakresy liczbowe (np. '158-170', typowe dla rozmiarow
    ciala dzieci) - rozmiar nie jest czescia formatu 'Typ + Model + in Kolor'."""
    if not text:
        return text, False
    new_text = text
    changed = False

    range_pattern = re.compile(r"\b\d{2,3}\s*[-–—]\s*\d{2,3}\b")
    if range_pattern.search(new_text):
        new_text = range_pattern.sub("", new_text)
        changed = True

    for token in TITLE_SIZE_TOKENS:
        pattern = re.compile(rf"\b{re.escape(token)}\b")
        if pattern.search(new_text):
            new_text = pattern.sub("", new_text)
            changed = True

    if not changed:
        return text, False
    new_text = re.sub(r"^[\s\-:,]+", "", new_text)
    new_text = re.sub(r"[\s\-:,]+$", "", new_text)
    new_text = re.sub(r"\s{2,}", " ", new_text).strip()
    return new_text, new_text != text


def _strip_trailing_mit_clause(text: str) -> tuple[str, bool]:
    """Usuwa ostatnia klauzule 'mit X' w tytule (od 'mit' do konca stringa) -
    zwykle stary opis wzoru/materialu, ktory zostanie zastapiony pelniejszym
    opisem w 'in {color_manufacturer_text}' (patrz ensure_in_before_color,
    wywolywane PO tej funkcji w process_product())."""
    if not text:
        return text, False
    matches = list(re.finditer(r"\bmit\b", text, re.IGNORECASE))
    if not matches:
        return text, False
    last = matches[-1]
    new_text = text[:last.start()]
    new_text = re.sub(r"[\s\-:,]+$", "", new_text)
    if not new_text:
        # cale zdanie to byla klauzula 'mit X' - nie zostawiaj pustego tytulu
        return text, False
    return new_text, True


# modelName_text/brandName w danych bywaja (zaobserwowane) blednie ustawione
# na caly tytul zamiast na krotki kod/nazwe modelu lub marki - w takim
# przypadku probe usuniecia/ochrony tego "fragmentu" nie zadzialalaby
# poprawnie (albo zablokowalaby cale czyszczenie tytulu, albo nie znalazlaby
# dopasowania wcale). Traktujemy jako prawdziwa, krotka wartosc (model/marke)
# tylko rozsadnie krotkie teksty (kod/nazwa produktu, nie pelne zdanie).
_MAX_SHORT_VALUE_WORDS = 4


def _looks_like_short_value(value: str) -> bool:
    return bool(value) and len(value.split()) <= _MAX_SHORT_VALUE_WORDS


def _find_model_span(text: str, model_name: str) -> tuple[int, int] | None:
    """Znajduje model_name w text bez wzgledu na wielkosc liter (w danych
    zdarza sie niespojnosc, np. modelName_text='G-Rose' a w tytule 'G-ROSE' -
    dopasowanie z rozroznianiem wielkosci liter nie wykrywaloby wtedy modelu,
    co pozwalaloby innym poprawkom go znieksztalcic - patrz fix_color_word
    tlumaczace 'ROSE' jako oddzielne slowo-kolor). Zwraca (start, end) w
    oryginalnym text, zeby zachowac oryginalna pisownie dopasowania."""
    if not model_name:
        return None
    m = re.search(re.escape(model_name), text, re.IGNORECASE)
    return (m.start(), m.end()) if m else None


def _strip_brand_name(text: str, brand_name: str) -> tuple[str, bool]:
    """Usuwa nazwe marki (brandName/Marke) z tekstu, gdziekolwiek wystepuje -
    marka nie powinna byc czescia tytulu w docelowym formacie 'Typ produktu +
    Model + in Kolor'. Tak jak modelName_text, brandName w danych bywa blednie
    ustawione na caly tytul - w takim przypadku nie probujemy nic usuwac
    (patrz _looks_like_short_value)."""
    brand_name = (brand_name or "").strip()
    if not brand_name or not text or not _looks_like_short_value(brand_name):
        return text, False
    pattern = re.compile(re.escape(brand_name), re.IGNORECASE)
    if not pattern.search(text):
        return text, False
    new_text = pattern.sub("", text)
    new_text = re.sub(r"^[\s\-:,]+", "", new_text)
    new_text = re.sub(r"[\s\-:,]+$", "", new_text)
    new_text = re.sub(r"\s{2,}", " ", new_text).strip()
    return new_text, new_text != text


def _category_type_tokens(category_tree, resolved_code: str | None) -> set[str]:
    """Zwraca stemowane tokeny (dlugosc >=4) z etykiety kategorii-liscia -
    sygnal do rozpoznania, ktora fraza w tytule jest prawdziwym 'Typem
    produktu' gdy jest ich wiecej niz jedna kandydatka (patrz
    strip_title_junk). Reuzywa _normalize/_stem z logic.categories - ta sama
    heurystyka co suggest_category()/current_category_supported_by_text()."""
    if not resolved_code or category_tree is None:
        return set()
    label = category_tree.label_for(resolved_code) or ""
    return {_cat_stem(t) for t in _cat_normalize(label).split() if len(t) >= 4}


def _phrase_match_score(phrase: str, category_tokens: set[str]) -> int:
    """Liczy ile category_tokens ma dopasowanie (substring w dowolna strone)
    z ktoregokolwiek stemowanego tokenu frazy - potrzebne bo tytuly bywaja
    jednym zlozonym slowem ('Softshelljacke'), a etykieta kategorii dwoma
    ('Softshell Jacken'): 'softshell' i 'jack' oba pasuja jako podciagi
    'softshelljack'."""
    if not phrase or not category_tokens:
        return 0
    phrase_tokens = {_cat_stem(t) for t in _cat_normalize(phrase).split() if len(t) >= 4}
    if not phrase_tokens:
        return 0
    return sum(
        1 for cat_tok in category_tokens
        if any(cat_tok in p or p in cat_tok for p in phrase_tokens)
    )


def strip_title_junk(
    title: str,
    model_name: str,
    brand_name: str = "",
    category_tokens: set[str] | None = None,
) -> tuple[str, bool]:
    """Usuwa z tytulu tresci niezgodne z docelowym formatem 'Typ produktu +
    Model + in Kolor' (zalozenie 1d w README.md): marke, slowa plci/demografii
    oraz - gdy prawdziwy model jest znaleziony w tytule (patrz
    _looks_like_short_value/_find_model_span) - fragment PO modelu (bo nic
    nie powinno nastepowac po modelu poza kolorem, ktory dostawia
    ensure_in_before_color). Gdy tytul ma DWIE kandydatki na 'Typ produktu'
    (przed modelem i po modelu - np. zdublowany opis typu), category_tokens
    (patrz _category_type_tokens) rozstrzyga ktora zatrzymac poprzez
    dopasowanie do etykiety kategorii-liscia (_phrase_match_score); bez
    category_tokens domyslnie wygrywa fragment przed modelem, tak jak
    dotychczas. Bez rozpoznanego modelu usuwana jest tylko ostatnia klauzula
    'mit X' (patrz _strip_trailing_mit_clause)."""
    if not title:
        return title, False
    model_name = (model_name or "").strip()
    if not _looks_like_short_value(model_name):
        model_name = ""

    def _clean_segment(segment: str) -> tuple[str, bool]:
        t, ch1 = _strip_gender_words(segment)
        t, ch2 = _strip_brand_name(t, brand_name)
        t, ch3 = _strip_size_tokens(t)
        t, ch4 = _strip_trailing_mit_clause(t)
        return t, ch1 or ch2 or ch3 or ch4

    span = _find_model_span(title, model_name) if model_name else None
    if span:
        before, model_actual, after = title[:span[0]], title[span[0]:span[1]], title[span[1]:]
        before_fixed, _ = _clean_segment(before)
        after_fixed, _ = _clean_segment(after)

        use_after_as_type = False
        if category_tokens:
            score_before = _phrase_match_score(before_fixed, category_tokens)
            score_after = _phrase_match_score(after_fixed, category_tokens)
            use_after_as_type = score_after > score_before

        kept_type = after_fixed if use_after_as_type else before_fixed
        parts = [kept_type.strip(), model_actual.strip()]
        new_title = " ".join(p for p in parts if p)
        return new_title, new_title != title

    new_title, changed = _clean_segment(title)
    return new_title, changed


def _normalize_for_compare(text: str) -> str:
    """Normalizuje spacje/lacznik, zeby porownac np. 'Sherpa Jacke' (w tytule)
    z 'Sherpa-Jacke' (modelName_text) jako ten sam tekst - w danych zdarzaja
    sie oba separatory dla tego samego modelu."""
    return re.sub(r"[\s\-]+", " ", text).strip().lower()


def ensure_model_present(title: str, model_name: str) -> tuple[str, bool]:
    """Jesli modelName_text nie wystepuje w tytule (nawet w formie z innym
    separatorem czlonow - patrz _normalize_for_compare) - dopisuje go na koncu
    tytulu. Wywolywane PRZED ensure_in_before_color w process_product(), zeby
    kolejnosc koncowa byla 'Typ produktu + Model + in Kolor'. Nie dopisuje nic,
    gdy model_name nie wyglada na prawdziwy, krotki model (patrz
    _looks_like_short_value) - inaczej blednie zdublowalibysmy caly tytul."""
    model_name = (model_name or "").strip()
    if not model_name or not title or not _looks_like_short_value(model_name):
        return title, False
    if _normalize_for_compare(model_name) in _normalize_for_compare(title):
        return title, False
    new_title = f"{title.rstrip()} {model_name}"
    return new_title, True


def apply_title_fixes_excluding_model(text: str, model_name: str) -> tuple[str, bool]:
    """Stosuje naprawy formatowania/jezyka do tytulu, ALE nie dotyka fragmentu
    bedacego nazwa modelu (dopasowanie bez wzgledu na wielkosc liter - patrz
    _find_model_span - bo modelName_text bywa zapisany inna wielkoscia liter
    niz w tytule, np. 'G-Rose' vs 'G-ROSE') - nazwa modelu/marki nie powinna
    byc tlumaczona. Ochrona dziala TYLKO gdy model_name wyglada na prawdziwy,
    krotki model (patrz _looks_like_short_value) - w danych zdarza sie, ze
    to pole bledne powiela caly tytul, co zablokowaloby wszystkie poprawki."""
    if not text:
        return text, False
    model_name = (model_name or "").strip()
    if not _looks_like_short_value(model_name):
        model_name = ""
    span = _find_model_span(text, model_name) if model_name else None
    if span:
        before, model_actual, after = text[:span[0]], text[span[0]:span[1]], text[span[1]:]
        before_fixed, ch1 = fix_double_spaces_and_grammar(before)
        before_fixed, ch1b = apply_translation_fixes(before_fixed)
        after_fixed, ch2 = fix_double_spaces_and_grammar(after)
        after_fixed, ch2b = apply_translation_fixes(after_fixed)
        new_text = before_fixed + model_actual + after_fixed
        return new_text, ch1 or ch1b or ch2 or ch2b
    new_text, ch1 = fix_double_spaces_and_grammar(text)
    new_text, ch2 = apply_translation_fixes(new_text)
    return new_text, ch1 or ch2



def fix_color_word(value: str) -> tuple[str, bool, str | None]:
    if not value:
        return value, False, None
    changed_any = False

    def _repl(m: re.Match) -> str:
        nonlocal changed_any
        word = m.group(0)
        de = D.COLORS_EN_TO_DE.get(word.lower())
        if de:
            changed_any = True
            return de
        return word

    # dopasowanie slow niezaleznie od otaczajacej interpunkcji (przecinki, kropki
    # itp.) - wazne zwlaszcza w tekscie prozy (Long Description), nie tylko
    # w krotkich, "czystych" polach jak tytul/kolor producenta
    new_value = re.sub(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", _repl, value)
    note = "Przetlumaczono angielska nazwe koloru na niemiecka." if changed_any else None
    return new_value, changed_any, note


def fix_material_composition(value: str) -> tuple[str, bool, str | None]:
    if not value:
        return value, False, None
    new_value = value
    changed = False
    # blad formatu procentow: "% 100 Baumwolle" -> "100% Baumwolle"
    m = re.search(r"%\s*(\d{1,3})\s+", new_value)
    if m:
        new_value = re.sub(r"%\s*(\d{1,3})\s+", r"\1% ", new_value)
        changed = True
    for en, de in D.MATERIALS_EN_TO_DE.items():
        if en == de:
            continue
        pattern = re.compile(rf"\b{re.escape(en)}\b")
        if pattern.search(new_value):
            new_value = pattern.sub(de, new_value)
            changed = True
    note = "Naprawiono format/jezyk skladu materialowego." if changed else None
    return new_value, changed, note


def singularize_de_label(label: str) -> str:
    """Bardzo prosta heurystyka liczby pojedynczej dla niemieckich etykiet kategorii,
    uzywana tylko jako fallback gdy manufacturer_product_type_text_de jest bezuzyteczny
    (obcojezyczny/zbyt ogolny jak "Textil")."""
    if label.endswith("en") and len(label) > 4:
        return label[:-2]
    if label.endswith("e"):
        return label[:-1]
    return label


def build_dimension_suffix(row: dict) -> str | None:
    """Buduje sufiks tytulu '(B)W x (H)H x (T)D cm' na podstawie pol wymiarow,
    jesli sa kompletne (potwierdzone kody: width_numeric/height_numeric/
    depth_numeric + warianty _unit, zrodlo: arkusz Columns z good_data_2.xlsx,
    potwierdzone tez wzorcem tytulu z przykladu 'Wandspiegel 3039 in Walnuss –
    (B)46 x (H)46 x (T)6 cm'). Zwraca None, jesli ktorykolwiek wymiar brakuje -
    nie zgadujemy brakujacych wartosci."""
    def _num(field):
        val = row.get(field)
        if val is None or str(val).strip() == "":
            return None
        try:
            f = float(str(val).replace(",", "."))
            return str(int(f)) if f == int(f) else str(f)
        except ValueError:
            return None

    width = _num("width_numeric")
    height = _num("height_numeric")
    depth = _num("depth_numeric")
    if width is None or height is None or depth is None:
        return None
    unit = str(row.get("width_numeric_unit") or row.get("height_numeric_unit") or "cm").strip() or "cm"
    return f"(B){width} x (H){height} x (T){depth} {unit}"


def _strip_old_dimensions(text: str) -> tuple[str, bool]:
    if not text:
        return text, False
    new_text = _OLD_DIMENSIONS_RE.sub("", text).rstrip()
    if not new_text:
        return text, False
    return new_text, new_text != text


def _strip_word(text: str, word: str) -> tuple[str, bool]:
    if not text or not word:
        return text, False
    pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
    if not pattern.search(text):
        return text, False
    new_text = pattern.sub("", text)
    new_text = re.sub(r"^[\s\-:,]+", "", new_text)
    new_text = re.sub(r"\s{2,}", " ", new_text).strip()
    return new_text, new_text != text


# --- ReferenceData (arkusz szablonu Mirakl z dozwolonymi wartosciami) ------------

def reference_values_from_rows(rows: list) -> dict[str, set[str]]:
    """Arkusz ReferenceData: wiersz 1 = kody pol, kolumny = dozwolone wartosci.
    Rozne szablony maja rozne wartosci dla tego samego pola (np. colors:
    'bunt'/'schwarz' w jednym, 'black' w innym), wiec to ten arkusz - a nie
    stale slowniki - jest zrodlem prawdy, gdy plik go ma."""
    if not rows:
        return {}
    header = rows[0]
    result: dict[str, set[str]] = {}
    for j, code in enumerate(header):
        if not isinstance(code, str) or not code.strip():
            continue
        values = {str(r[j]).strip() for r in rows[1:]
                  if r is not None and j < len(r) and r[j] is not None and str(r[j]).strip()}
        if values:
            result[code.strip()] = values
    return result


# pola z ReferenceData, ktorych NIE walidujemy ta sciezka - kategoria ma
# wlasna logike (resolve + sugestie) w process_product
_REFERENCE_SKIP_FIELDS = {"CATEGORY"}


def check_reference_value(field_code: str, value: str, allowed: set[str]) -> str | None:
    """Zwraca komunikat bledu albo None. Wartosci wielokrotne rozdzielone '|'
    albo przecinkiem (zaobserwowane w LI_Softshell: 'Fahrrad, Funsport, Laufen');
    przecinek probujemy dopiero, gdy caly token nie jest dozwolony - niektore
    dozwolone wartosci same zawieraja przecinek ('230 V AC, 50 Hz'). Przy
    liscie z przecinkami raportujemy tylko niedozwolone elementy."""
    tokens = [t.strip() for t in str(value).split("|") if t.strip()]
    lower_map = {a.lower(): a for a in allowed}
    problems = []
    for t in tokens:
        if t in allowed:
            continue
        # caly token niedozwolony - moze to lista rozdzielona przecinkami
        parts = [p.strip() for p in t.split(",") if p.strip()]
        if len(parts) < 2 or not any(p in allowed or p.lower() in lower_map for p in parts):
            parts = [t]
        for part in parts:
            if part in allowed:
                continue
            proper = lower_map.get(part.lower())
            if proper:
                problems.append(f"'{part}' -> powinno byc '{proper}' (wielkosc liter)")
            else:
                problems.append(f"'{part}' nie wystepuje w ReferenceData")
    if not problems:
        return None
    hint = ""
    if len(allowed) <= 40:
        hint = f" Dozwolone: {sorted(allowed)}."
    return f"Pole {field_code}: " + "; ".join(problems) + "." + hint


# --- grupy wariantow ---------------------------------------------------------------

def _concrete_color_words(reference_values: dict | None) -> set[str]:
    words = set(D.COLORS_VALID_CODES) | {k.lower() for k in D.COLORS_EN_TO_DE} | \
        {v.lower() for v in D.COLORS_EN_TO_DE.values()}
    if reference_values and "colors" in reference_values:
        words |= {v.lower() for v in reference_values["colors"]}
    return words - MULTICOLOR_WORDS


def _split_color_prefix(value: str) -> tuple[str, str] | None:
    """'Mehrfarbig - Flamingo' -> ('Mehrfarbig - ', 'Flamingo'); bez separatora -> None."""
    m = _COLOR_PREFIX_SEPARATOR_RE.search(value or "")
    if not m or not value[:m.start()].strip() or not value[m.end():].strip():
        return None
    return value[:m.end()], value[m.end():]


def variant_group_color_prefixes(products: list, reference_values: dict | None = None) -> list[str | None]:
    """Dla kazdego produktu zwraca prefiks color_manufacturer_text do usuniecia
    (np. 'Mehrfarbig - ') albo None.

    Glowna regula (potwierdzona przez uzytkownika): w grupie wariantow kolor
    producenta ma ROZROZNIAC produkty. Gdy wszystkie produkty grupy maja kolor
    w postaci '<wspolny prefiks><separator><motyw>' i motywy sie roznia, to
    wspolny prefiks niczego nie rozroznia - usuwamy go, zostaje sam motyw.
    Nie ruszamy grup, w ktorych prefiks to konkretny kolor (np.
    'Schwarz - Weiß' / 'Schwarz - Rot' to kolory dwubarwne, nie motywy).

    Fallback dla produktow, ktorych nie obejmuje regula grupy (brak grupy,
    grupa jednoelementowa, identyczne kolory w grupie): prefiks usuwany tylko,
    gdy jest znanym okresleniem wielokolorowosci (MULTICOLOR_WORDS)."""
    color_field = None
    group_field = None
    if products:
        color_field = _first_present(products[0], COLOR_MANUFACTURER_CANDIDATES)
        group_field = _first_present(products[0], VARIANT_GROUP_CANDIDATES)
    result: list[str | None] = [None] * len(products)
    if not color_field:
        return result

    concrete = _concrete_color_words(reference_values)
    groups: dict[str, list[int]] = {}
    if group_field:
        for i, row in enumerate(products):
            g = str(row.get(group_field) or "").strip()
            if g:
                groups.setdefault(g, []).append(i)

    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        splits = [_split_color_prefix(str(products[i].get(color_field) or "")) for i in idxs]
        if any(s is None for s in splits):
            continue
        prefixes = {s[0] for s in splits}
        motifs = {s[1].strip() for s in splits}
        if len(prefixes) != 1 or len(motifs) < 2:
            continue
        prefix = prefixes.pop()
        prefix_word = _COLOR_PREFIX_SEPARATOR_RE.sub("", prefix).strip().lower()
        if prefix_word in concrete:
            continue
        for i in idxs:
            result[i] = prefix

    for i, row in enumerate(products):
        if result[i] is not None:
            continue
        split = _split_color_prefix(str(row.get(color_field) or ""))
        if split and _COLOR_PREFIX_SEPARATOR_RE.sub("", split[0]).strip().lower() in MULTICOLOR_WORDS:
            result[i] = split[0]
    return result


def harmonize_variant_group_titles(products: list, motif_rows: list[bool]) -> dict[int, list[dict]]:
    """Po przetworzeniu wierszy: w grupie wariantow tytuly powinny miec wspolny
    rdzen ('<rdzen> in <kolor>') i roznic sie tylko kolorem/motywem
    (potwierdzone przez uzytkownika - dopiski typu '6-sprachig', 'Vintage
    Poster', 'aus Recyclingpapier' wystepujace tylko w czesci wariantow sa
    usuwane). Rdzen = wspolny poczatek (cale slowa) rdzeni wszystkich
    wariantow. Dotyczy TYLKO grup, w ktorych kolor producenta to motyw
    (motif_rows - wszystkie wiersze grupy mialy usuniety prefiks koloru, patrz
    variant_group_color_prefixes). W innych grupach rdzen bywa nazwa wlasna
    wariantu (np. 'Babydecke "Sanftes Rosenholz" in Rosa') i nie wolno go
    obcinac. Modyfikuje products w miejscu, zwraca {indeks: [issue]}."""
    issues: dict[int, list[dict]] = {}
    if not products:
        return issues
    title_field = _first_present(products[0], TITLE_CODE_CANDIDATES)
    color_field = _first_present(products[0], COLOR_MANUFACTURER_CANDIDATES)
    group_field = _first_present(products[0], VARIANT_GROUP_CANDIDATES)
    if not (title_field and color_field and group_field):
        return issues

    groups: dict[str, list[int]] = {}
    for i, row in enumerate(products):
        g = str(row.get(group_field) or "").strip()
        if g:
            groups.setdefault(g, []).append(i)

    for idxs in groups.values():
        if len(idxs) < 2 or not all(motif_rows[i] for i in idxs):
            continue
        parts = []
        for i in idxs:
            title = str(products[i].get(title_field) or "")
            color = str(products[i].get(color_field) or "").strip()
            pos = title.rfind(f" in {color}") if color else -1
            if pos <= 0:
                parts = None
                break
            parts.append((i, title, title[:pos], title[pos:]))
        if not parts:
            continue
        # ujednolicamy tylko, gdy sama czesc 'in <kolor>...' rozroznia warianty -
        # jesli dwa rozne tytuly mialyby ten sam kolor (np. "Babydecke "Sanftes
        # Rosenholz" in Rosa" i "Babydecke "geruhsamer Rosentau" in Rosa"), to
        # rdzen niesie informacje rozrozniajaca i nie wolno go obcinac
        if len({p[3] for p in parts}) < len({p[1] for p in parts}):
            continue
        cores = [p[2].split() for p in parts]
        if all(c == cores[0] for c in cores):
            continue
        common = []
        for words in zip(*cores):
            if len(set(words)) != 1:
                break
            common.append(words[0])
        if not any(re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]{3,}", w) for w in common):
            for i, title, _, _ in parts:
                issues.setdefault(i, []).append(issue(
                    title_field, "TITLE_GROUP_INCONSISTENT",
                    f"Tytuly w grupie wariantow nie maja wspolnego rdzenia ('{title}') - "
                    f"do recznej weryfikacji.", "manual_review"))
            continue
        common_core = " ".join(common)
        for i, title, core, rest in parts:
            if core.split() == common:
                continue
            new_title = common_core + rest
            products[i][title_field] = new_title
            removed = " ".join(core.split()[len(common):])
            issues.setdefault(i, []).append(issue(
                title_field, "TITLE_GROUP_HARMONIZED",
                f"Ujednolicono tytul z grupa wariantow (usunieto '{removed}'): "
                f"'{title}' -> '{new_title}'.", "auto_fixed"))
    return issues


def process_products(products: list, category_tree: CategoryTree,
                     reference_values: dict | None = None) -> list[tuple[OrderedDict, list[dict]]]:
    """Przetwarza caly plik: reguly wymagajace widoku grupy wariantow (prefiks
    koloru, spojnosc tytulow) + process_product dla kazdego wiersza."""
    prefixes = variant_group_color_prefixes(products, reference_values)
    results = [process_product(row, category_tree, color_prefix=prefixes[i],
                               reference_values=reference_values)
               for i, row in enumerate(products)]
    group_issues = harmonize_variant_group_titles([r[0] for r in results],
                                                  [p is not None for p in prefixes])
    for i, extra in group_issues.items():
        results[i][1].extend(extra)
    return results


# --- glowna funkcja per-produkt --------------------------------------------------

def process_product(row: OrderedDict, category_tree: CategoryTree, color_prefix: str | None = None,
                    reference_values: dict | None = None) -> tuple[OrderedDict, list[dict]]:
    """color_prefix - patrz variant_group_color_prefixes; reference_values -
    patrz reference_values_from_rows (None = plik bez arkusza ReferenceData)."""
    new_row = OrderedDict(row)
    issues: list[dict] = []

    sku = new_row.get("shop_sku") or new_row.get("Shop SKU") or "?"

    # --- kategoria: normalizacja formatu + sprawdzenie zgodnosci z trescia ------
    cat_code_field = _first_present(new_row, CATEGORY_CODE_CANDIDATES)
    resolved_code = None
    if cat_code_field:
        raw_cat = new_row[cat_code_field]
        resolved_code = category_tree.resolve(raw_cat)
        if resolved_code is None:
            issues.append(issue(cat_code_field, "CATEGORY_UNRESOLVED",
                                 f"Nie udalo sie rozpoznac kategorii '{raw_cat}' w all_categories.json.",
                                 "manual_review"))
        else:
            title_field = _first_present(new_row, TITLE_CODE_CANDIDATES)
            title_text = new_row.get(title_field, "") if title_field else ""
            desc_field = _first_present(new_row, DESC_CODE_CANDIDATES)
            desc_text = new_row.get(desc_field, "") if desc_field else ""
            check_text = f"{title_text} {desc_text}"

            # Dedykowana, precyzyjna regula: wzorzec "X tlg./er-Set" w tytule a
            # kategoria "pojedyncza" -> sprawdz czy istnieje kategoria-siostra "*-Sets"
            is_set_title = bool(re.search(r"\b\d+\s*(-|\s)?tlg\.?|\b\d+\s*er[- ]?set\b",
                                           str(title_text), re.IGNORECASE))
            set_sibling = category_tree.find_set_sibling(resolved_code) if is_set_title else None
            if set_sibling:
                issues.append(issue(
                    cat_code_field, "CATEGORY_SET_MISMATCH",
                    (f"Tytul wskazuje na zestaw wieloczesciowy ('{title_text}'), ale produkt "
                     f"jest w kategorii '{category_tree.path_de(resolved_code)}' (kod {resolved_code}) "
                     f"zamiast w dedykowanej kategorii zestawow "
                     f"'{category_tree.path_de(set_sibling)}' (kod {set_sibling})."),
                    "manual_review"))
                suggestions = []  # unikamy podwojnego zgloszenia tego samego produktu
            else:
                suggestions = category_tree.suggest_category(check_text, top_n=1)
            if suggestions:
                best_code, best_path, score = suggestions[0]
                current_supported = category_tree.current_category_supported_by_text(resolved_code, check_text)
                # Flagujemy tylko gdy: sugerowana kategoria != obecna, wynik dopasowania
                # jest sensowny (>=2), a slowo-klucz obecnej kategorii W OGOLE nie
                # wystepuje w tresci produktu (czyli obecne przypisanie wyglada podejrzanie).
                if best_code != resolved_code and score >= 2 and not current_supported:
                    issues.append(issue(
                        cat_code_field, "CATEGORY_MISMATCH_SUSPECTED",
                        (f"Tresc produktu ('{title_text}') sugeruje kategorie "
                         f"'{best_path}' (kod {best_code}), a produkt jest przypisany do "
                         f"'{category_tree.path_de(resolved_code)}' (kod {resolved_code}). "
                         f"Sugerowana zmiana wymaga potwierdzenia."),
                        "manual_review"))

    # --- kolor producenta: EN -> DE (przed tytulem, zeby uzyc poprawnej wartosci
    # przy ustawianiu 'in' przed kolorem w tytule) ---------------------------------
    color_field = _first_present(new_row, COLOR_MANUFACTURER_CANDIDATES)
    if color_field and new_row.get(color_field):
        new_val, changed, note = fix_color_word(str(new_row[color_field]))
        if changed:
            new_row[color_field] = new_val
            issues.append(issue(color_field, "COLOR_LANGUAGE_FIXED", note, "auto_fixed"))

    # --- kolor producenta: usuniecie wspolnego prefiksu grupy wariantow
    # ("Mehrfarbig - Flamingo" -> "Flamingo"), patrz variant_group_color_prefixes -
    prefix_word = None
    if color_prefix and color_field and str(new_row.get(color_field) or "").startswith(color_prefix):
        old_val = str(new_row[color_field])
        motif = old_val[len(color_prefix):].strip()
        new_row[color_field] = motif
        prefix_word = _COLOR_PREFIX_SEPARATOR_RE.sub("", color_prefix).strip()
        issues.append(issue(color_field, "COLOR_PREFIX_REMOVED",
                             f"Usunieto wspolny prefiks '{prefix_word}' z koloru producenta: "
                             f"'{old_val}' -> '{motif}' (motyw rozroznia warianty).",
                             "auto_fixed"))

    # --- tytul: docelowy format "Typ produktu + Model + in Kolor" (zalozenie
    # 1d w README.md) - usuwamy smieci (plec, stary opis wzoru dublujacy
    # kolor), naprawiamy jezyk/formatowanie, upewniamy sie ze model i kolor sa
    # obecne (poza nazwa modelu, ktora nigdy nie jest modyfikowana) ---------
    title_field = _first_present(new_row, TITLE_CODE_CANDIDATES)
    if title_field and new_row.get(title_field):
        text = str(new_row[title_field])
        model_name = str(new_row.get("modelName_text", "") or "")
        sku_value = str(new_row.get("shop_sku") or new_row.get("Shop SKU") or "")
        if model_name.strip() and model_name.strip() == sku_value.strip():
            # modelName_text bywa (zaobserwowane) uzywane jako wewnetrzny kod
            # artykulu identyczny z shop_sku, a nie prawdziwa nazwa modelu
            # (np. "BV6741-412-XL") - nie doklejamy takiego kodu do tytulu.
            model_name = ""
        if prefix_word:
            # kolor to motyw wariantu (np. "Flamingo") - pelni role modelu,
            # a modelName_text bywa wtedy ta sama wartoscia albo angielskim
            # odpowiednikiem ("Cat" vs "Katze") - nie wstawiamy go osobno
            model_name = ""
        brand_name = str(new_row.get("brandName") or new_row.get("Marke") or "")
        category_tokens = _category_type_tokens(category_tree, resolved_code)

        dim_suffix = build_dimension_suffix(new_row)
        base = text
        pre_changed = False
        if dim_suffix:
            base, ch_dims = _strip_old_dimensions(base)
            pre_changed = pre_changed or ch_dims
        if prefix_word:
            base, ch_prefix = _strip_word(base, prefix_word)
            pre_changed = pre_changed or ch_prefix

        text4, changed = strip_title_junk(base, model_name, brand_name, category_tokens)
        changed = changed or pre_changed

        text_translated, ch_translate = apply_title_fixes_excluding_model(text4, model_name)
        text4 = text_translated
        changed = changed or ch_translate

        text_model, ch_model = ensure_model_present(text4, model_name)
        text4 = text_model
        changed = changed or ch_model

        # Dopisanie/naprawa 'in' przed kolorem (kolor = aktualna wartosc
        # color_manufacturer_text, juz po ew. tlumaczeniu powyzej)
        color_value = str(new_row.get(color_field, "") or "") if color_field else ""
        if not color_value:
            # brak color_manufacturer_text w ogole (nie tylko puste, ale pole
            # nie istnieje w pliku) - probujemy jawnie oznaczonej wzmianki
            # "Farbe: X" w Long Description, patrz extract_color_from_description
            desc_field_fallback = _first_present(new_row, DESC_CODE_CANDIDATES)
            desc_for_color = str(new_row.get(desc_field_fallback, "") or "") if desc_field_fallback else ""
            extracted = extract_color_from_description(desc_for_color)
            if extracted:
                color_value = extracted
                text4, ch_color_cleanup = _strip_bare_color_word_fragments(text4, color_value)
                changed = changed or ch_color_cleanup
        text5, changed_in = ensure_in_before_color(text4, color_value)
        if changed_in:
            text4 = text5
            changed = True

        if changed:
            new_row[title_field] = text4
            issues.append(issue(title_field, "TITLE_FORMAT_FIXED",
                                 f"Naprawiono formatowanie/jezyk tytulu: '{text}' -> '{text4}'.",
                                 "auto_fixed"))
        if " in " not in text4 and " im " not in text4:
            issues.append(issue(title_field, "TITLE_PATTERN_MISSING",
                                 "Tytul nie zawiera wzorca 'Typ (+Model) in Farbe' - wymaga recznej weryfikacji.",
                                 "manual_review"))

        # Wymiary w tytule (onesize/hardgoods): wzorzec "... – (B)W x (H)H x (T)D cm"
        # potwierdzony przykladem "Wandspiegel 3039 in Walnuss – (B)46 x (H)46 x (T)6 cm"
        has_dim_in_title = bool(re.search(r"\(B\)|\(H\)|\(T\)", text4))
        if dim_suffix and not has_dim_in_title:
            new_title = f"{text4} – {dim_suffix}"
            new_row[title_field] = new_title
            issues.append(issue(title_field, "TITLE_DIMENSIONS_ADDED",
                                 f"Dodano wymiary do tytulu na podstawie pol width/height/depth_numeric: '{new_title}'.",
                                 "auto_fixed"))

    # --- Long Description: te same poprawki jezykowe (EN->DE slowa koloru/materialu) -
    desc_field = _first_present(new_row, DESC_CODE_CANDIDATES)
    if desc_field and new_row.get(desc_field):
        desc_text = str(new_row[desc_field])
        desc_fixed, desc_changed = apply_translation_fixes(desc_text)
        if desc_changed:
            new_row[desc_field] = desc_fixed
            issues.append(issue(desc_field, "DESCRIPTION_LANGUAGE_FIXED",
                                 "Naprawiono jezyk/pisownie w Long Description (angielskie "
                                 "slowa koloru/materialu, uciete znaki specjalne).",
                                 "auto_fixed"))

    # --- material: format % + jezyk ----------------------------------------------
    mat_field = _first_present(new_row, MATERIAL_CODE_CANDIDATES)
    if mat_field and new_row.get(mat_field):
        new_val, changed, note = fix_material_composition(str(new_row[mat_field]))
        if changed:
            new_row[mat_field] = new_val
            issues.append(issue(mat_field, "MATERIAL_FORMAT_FIXED", note, "auto_fixed"))

    # --- pola ze slownikiem w arkuszu ReferenceData: walidacja wzgledem niego
    # (zastepuje stale slowniki ponizej dla tych pol) ------------------------------
    reference_values = reference_values or {}
    for field_code, allowed in reference_values.items():
        if field_code in _REFERENCE_SKIP_FIELDS or not new_row.get(field_code):
            continue
        note = check_reference_value(field_code, str(new_row[field_code]), allowed)
        if note:
            issues.append(issue(field_code, "REFERENCE_VALUE_INVALID", note, "manual_review"))

    # --- genders / ages: tylko flagowanie (bez auto-tlumaczenia - poza zakresem) -
    gender_field = _first_present(new_row, GENDER_CODE_CANDIDATES)
    if gender_field and gender_field not in reference_values and new_row.get(gender_field):
        _, _, note = normalize_gender(str(new_row[gender_field]))
        if note:
            issues.append(issue(gender_field, "GENDER_UNKNOWN_VALUE", note, "manual_review"))

    age_field = _first_present(new_row, AGE_CODE_CANDIDATES)
    if age_field and age_field not in reference_values and new_row.get(age_field):
        _, _, note = normalize_age(str(new_row[age_field]))
        if note:
            issues.append(issue(age_field, "AGE_UNKNOWN_VALUE", note, "manual_review"))

    # --- colors: sprawdzenie wzgledem zamknietego slownika kodow -----------------
    colors_field = _first_present(new_row, COLORS_FIELD_CANDIDATES)
    if colors_field and colors_field not in reference_values and new_row.get(colors_field):
        _, _, note = normalize_colors_field(str(new_row[colors_field]))
        if note:
            issues.append(issue(colors_field, "COLOR_CODE_INVALID", note, "manual_review"))

    # --- modelName_text zanieczyszczony kolorem/rozmiarem (wykrycie, bez auto-fix) -
    model_field = "modelName_text" if "modelName_text" in new_row else None
    if model_field and new_row.get(model_field):
        model_val = str(new_row[model_field])
        size_val = str(new_row.get("sizes", "") or "")
        color_val = str(new_row.get(color_field, "") if color_field else "")
        # przy kolorze-motywie (patrz variant_group_color_prefixes) model rowny
        # kolorowi jest oczekiwany, a nie zanieczyszczeniem
        color_in_model = (not prefix_word and color_val
                          and color_val.lower() in model_val.lower() and len(color_val) > 2)
        if (size_val and size_val.lower() in model_val.lower()) or color_in_model:
            issues.append(issue(model_field, "MODEL_NAME_POLLUTED",
                                 f"Pole modelu '{model_val}' zdaje sie zawierac kolor/rozmiar - do recznej weryfikacji.",
                                 "manual_review"))

    # --- brandName: wykrycie formatu (numeryczne ID vs tekst) - tylko raportowanie -
    brand_field = "brandName" if "brandName" in new_row else ("Marke" if "Marke" in new_row else None)
    if brand_field and new_row.get(brand_field):
        brand_val = str(new_row[brand_field]).strip()
        if brand_val.isdigit():
            issues.append(issue(brand_field, "BRAND_IS_NUMERIC_ID",
                                 f"Marka podana jako numeryczne ID ({brand_val}) - brak slownika do weryfikacji nazwy.",
                                 "manual_review"))

    # --- pola enumeracyjne: sprawdzenie wzgledem znanych tokenow -----------------
    for field_code, allowed in D.KNOWN_ENUM_TOKENS.items():
        if field_code in reference_values:
            continue
        if field_code in new_row and new_row.get(field_code):
            raw = str(new_row[field_code])
            tokens = [t.strip() for t in raw.split("|") if t.strip()]
            unknown = [t for t in tokens if t not in allowed]
            if unknown:
                issues.append(issue(field_code, "ENUM_UNKNOWN_TOKEN",
                                     f"Nieznane tokeny {unknown} w polu {field_code} - brak w zaobserwowanym slowniku.",
                                     "manual_review"))

    return new_row, issues
