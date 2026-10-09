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
from . import field_requirements as FR
from . import title_rules as TR
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
    "männer", "frauen", "erwachsene",
    # niderlandzkie - tytuly DE bywaja (zaobserwowane) nieprzetlumaczone:
    # "Racerback - Dames Sportbh" (eksport Muchachomalo)
    "dames", "heren", "jongens", "meisjes", "kinderen",
}

# ogolne nazwy dzialu/kategorii doklejane jako osobny czlon tytulu po
# myslniku ("Boxershorts – 3er-Pack – Herren Unterhosen", "BH –
# Mädchenunterwäsche") - to kategoria, nie typ produktu, wiec nie powinna byc
# w tytule (eksport Muchachomalo, 2026-10-07). Usuwane tylko jako caly czlon
# po myslniku, takze z przyklejonym slowem plci ("Damenunterwäsche").
TITLE_GENERIC_CATEGORY_WORDS = {
    "unterwäsche", "unterhosen", "wäsche", "bademode", "nachtwäsche", "dessous",
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

# Mojibake: UTF-8 odczytany jako cp1252 ("AnhÃ¤nger", "groÃŸer") - zaobserwowane
# w ce-product-export (Lucardi). Para znakow Ã/Â + znak z gornej polowy cp1252.
_MOJIBAKE_RE = re.compile(
    "[\u00c2\u00c3][\u0080-\u00bf\u0152\u0153\u0160\u0161\u0178\u017d\u017e"
    "\u0192\u02c6\u02dc\u2013\u2014\u2018\u2019\u201a\u201c\u201d\u201e"
    "\u2020\u2021\u2022\u2026\u2030\u2039\u203a\u20ac\u2122]")


def _fix_mojibake_pair(m: re.Match) -> str:
    try:
        return m.group(0).encode("cp1252").decode("utf-8")
    except UnicodeError:
        return m.group(0)


def fix_broken_umlauts(text: str) -> tuple[str, bool]:
    if not text:
        return text, False
    new_text = _MOJIBAKE_RE.sub(_fix_mojibake_pair, text)
    changed = new_text != text
    for broken, fixed in D.BROKEN_UMLAUT_WORDS.items():
        if broken == "Weiss":  # to nie jest blad, tylko wariant pisowni - pomijamy
            continue
        pattern = re.compile(rf"\b{re.escape(broken)}\b")
        if pattern.search(new_text):
            new_text = pattern.sub(fixed, new_text)
            changed = True
    return new_text, changed


def fix_double_spaces_and_grammar(text: str, color_pattern: bool = True) -> tuple[str, bool]:
    """color_pattern=False - tytul bez 'in Farbe' (patrz title_rules), wtedy
    'im' jest zwyklym przyimkiem i nie zamieniamy go na 'in'."""
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
    if not color_pattern or re.search(r"\bin\b", new_text):
        return new_text, changed
    fixed = re.sub(r"\bim\b(?=\s+[A-ZÄÖÜ])", "in", new_text)
    if fixed != new_text:
        new_text = fixed
        changed = True
    return new_text, changed


def _all_tokens_valid(value: str, valid: set[str]) -> bool:
    """Pola wielowartosciowe rozdzielone '|' (np. ages 'child|baby' w
    poprawnym eksporcie ce-product-export) - kazdy token musi byc poprawnym kodem."""
    tokens = [t.strip() for t in value.split("|")]
    return all(t in valid for t in tokens)


def normalize_gender(value: str) -> tuple[str, bool, str | None]:
    """Poprawna wartosc pola to CODE: female/male/unisex (angielski - code i label
    sa tu identyczne). Zgodnie z ustaleniem o ograniczonym zakresie auto-tlumaczenia,
    wartosc NIE jest tu zmieniana automatycznie - tylko flagowana z podpowiedzia
    poprawnego kodu."""
    if not value:
        return value, False, None
    v = value.strip()
    if _all_tokens_valid(v, D.GENDERS_VALID_CODES):
        return v, False, None
    suggestion = D.GENDERS_LABEL_TO_CODE.get(v.lower())
    if suggestion:
        return v, False, (f"Value '{v}' is not a valid field code - "
                           f"should be: '{suggestion}'.")
    return v, False, (f"Unknown gender value '{v}' - expected one of the codes: "
                       f"{sorted(D.GENDERS_VALID_CODES)}.")


def normalize_age(value: str) -> tuple[str, bool, str | None]:
    """Poprawna wartosc pola to CODE: baby/child/adult (angielski, l. pojedyncza).
    Zgodnie z ustaleniem o ograniczonym zakresie auto-tlumaczenia, wartosc NIE
    jest tu zmieniana automatycznie - tylko flagowana z podpowiedzia poprawnego
    kodu."""
    if not value:
        return value, False, None
    v = value.strip()
    if _all_tokens_valid(v, D.AGES_VALID_CODES):
        return v, False, None
    suggestion = D.AGES_LABEL_TO_CODE.get(v.lower())
    if suggestion:
        return v, False, (f"Value '{v}' is a label, not a valid field code - "
                           f"should be: '{suggestion}'.")
    return v, False, (f"Unknown age group value '{v}' - expected one of the codes: "
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
            problems.append(f"'{t}' -> should be '{suggestion}'")
        else:
            problems.append(f"'{t}' - unknown color code")
    if problems:
        return value, False, "Field colors: " + "; ".join(problems) + "."
    return value, False, None


def apply_translation_fixes(text: str, protected: tuple[str, ...] = (),
                            prose: bool = False) -> tuple[str, bool]:
    """Wspolny zestaw poprawek jezykowych (EN->DE slowa koloru/materialu +
    naprawa ucietych znakow specjalnych) - uzywany w tytule i w Long Description.
    protected - nazwy modeli, ktorych nie tlumaczymy (opis wspomina tez modele
    innych produktow z pliku: "kombinierbar mit der Velvet Rose Kette" nie moze
    stac sie "Velvet Rosa"); prose - patrz fix_color_word."""
    if not text:
        return text, False
    if protected:
        pattern = re.compile("|".join(re.escape(p) for p in sorted(protected, key=len, reverse=True)),
                             re.IGNORECASE)
        parts, last, changed_any = [], 0, False
        for m in pattern.finditer(text):
            seg, ch = apply_translation_fixes(text[last:m.start()], prose=prose)
            parts += [seg, m.group(0)]
            changed_any = changed_any or ch
            last = m.end()
        seg, ch = apply_translation_fixes(text[last:], prose=prose)
        parts.append(seg)
        return "".join(parts), changed_any or ch
    changed_any = False
    new_text, ch = fix_broken_umlauts(text)
    changed_any = changed_any or ch
    new_text, ch, _ = fix_color_word(new_text, prose=prose)
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
    # cala fraza razem z "für" i spojnikami ("für Kinder", "Kinder und
    # Erwachsene") - samo slowo zostawialoby osierocone "für"/"und"
    alt = "|".join(sorted(map(re.escape, TITLE_GENDER_WORDS), key=len, reverse=True))
    pattern = re.compile(
        rf"(?:\b(?:für|for)\s+)?\b(?:{alt})\b(?:\s*(?:und|&|/|,)\s*\b(?:{alt})\b)*"
        rf"(?:-(?=[A-Za-zÄÖÜäöüß]))?",  # "Kinder-Hausschuhe" -> "Hausschuhe"
        re.IGNORECASE)
    new_text = pattern.sub("", text)
    changed = new_text != text
    if not changed:
        return text, False
    # sprzataj slady po usunieciu (osierocone laczniki/dwukropki/przecinki na
    # brzegach, nadmiarowe spacje)
    new_text = re.sub(r"^[\s\-–—:,]+", "", new_text)
    new_text = re.sub(r"[\s\-–—:,]+$", "", new_text)
    new_text = re.sub(r"\s{2,}", " ", new_text).strip()
    return new_text, new_text != text


def _strip_generic_category_segments(text: str) -> tuple[str, bool]:
    """Usuwa czlony tytulu po myslniku, ktore sa sama nazwa kategorii
    (TITLE_GENERIC_CATEGORY_WORDS, opcjonalnie z plcia): "Boxershorts –
    2er-Pack – Jungen Unterwäsche" -> "Boxershorts – 2er-Pack". Pierwszy
    czlon (typ produktu) nigdy nie jest usuwany, a myslnik musi byc
    poprzedzony spacja - "Sport-Unterwäsche" zostaje."""
    if not text:
        return text, False
    words = "|".join(sorted(map(re.escape, TITLE_GENERIC_CATEGORY_WORDS), key=len, reverse=True))
    genders = "|".join(sorted(map(re.escape, TITLE_GENDER_WORDS), key=len, reverse=True))
    pattern = re.compile(
        rf"\s+[-–—]\s*(?:(?:{genders})[\s-]*)?(?:{words})(?=\s+[-–—]|\s+i[nm]\s|\s*$)",
        re.IGNORECASE)
    new_text = pattern.sub("", text).strip()
    return new_text, new_text != text


def _translate_title_nl_words(text: str) -> tuple[str, bool]:
    """Niderlandzkie slowa typu produktu w tytule -> niemieckie
    (D.TITLE_NL_TO_DE): "Racerback - Sportbh" -> "Racerback - Sport-BH"."""
    new_text = text
    for nl, de in D.TITLE_NL_TO_DE.items():
        new_text = re.sub(rf"\b{re.escape(nl)}\b", de, new_text, flags=re.IGNORECASE)
    return new_text, new_text != text


def _strip_size_tokens(text: str) -> tuple[str, bool]:
    """Usuwa z tytulu wzmianki o rozmiarze - literowe kody (TITLE_SIZE_TOKENS:
    S/M/L/XL/...) oraz zakresy liczbowe (np. '158-170', typowe dla rozmiarow
    ciala dzieci) - rozmiar nie jest czescia formatu 'Typ + Model + in Kolor'."""
    if not text:
        return text, False
    new_text = text
    changed = False

    # "Größe 37", "Gr. 38/39", "Einheitsgröße", "One Size" - rozmiar jest w
    # polu sizes (eksport Morethansocks: "Gartenclogs - Größe 37 - Braun")
    size_word = re.compile(
        r"\b(?:Größe|Grösse|Gr\.)\s*\d+(?:[.,]\d+)?(?:\s*[-/]\s*\d+(?:[.,]\d+)?)?"
        r"|\b(?:Einheitsgröße|Einheitsgrösse|One[\s-]?Size)\b", re.IGNORECASE)
    if size_word.search(new_text):
        new_text = size_word.sub("", new_text)
        changed = True

    # zakres z jednostka ("Sattel 30-40 cm") to wymiar, nie rozmiar ciala
    range_pattern = re.compile(
        r"\b\d{2,3}\s*[-–—]\s*\d{2,3}\b(?!\s*(?:mm|cm|m|kg|g|ml|l|zoll|\"|%)(?![A-Za-z]))",
        re.IGNORECASE)
    if range_pattern.search(new_text):
        new_text = range_pattern.sub("", new_text)
        changed = True

    sizes_alt = "|".join(sorted(map(re.escape, TITLE_SIZE_TOKENS), key=len, reverse=True))
    compound = re.compile(rf"\b(?:{sizes_alt})\s*[-/]\s*(?:{sizes_alt})\b")
    if compound.search(new_text):
        new_text = compound.sub("", new_text)
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
    if re.search(r"anteil\b", text[last.start():], re.IGNORECASE):
        # "Schal mit Seidenanteil" - udzial materialu wymagany w tytule (Schals)
        return text, False
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


_BRAND_CHAR_VARIANTS = {
    "ä": "(?:ä|ae|a)", "ö": "(?:ö|oe|o)", "ü": "(?:ü|ue|u)", "ß": "(?:ß|ss)",
    "é": "[ée]", "è": "[èe]", "á": "[áa]", "à": "[àa]",
}
_BRAND_APOSTROPHES = "'´`’‘"
_BRAND_WORD_CHAR = r"[0-9A-Za-zÀ-ÖØ-öø-ÿß]"


def _brand_pattern(brand_name: str) -> re.Pattern | None:
    """Wzorzec marki odporny na zaobserwowane/typowe roznice zapisu miedzy
    brandName a tytulem: wielkosc liter, spacja/lacznik/kropka/brak miedzy
    czlonami ("BlueStar" / "Blue Star" / "Blue-Star", "SWISS KOPPER" /
    "Swiss-Kopper"), warianty apostrofu ("Les P´tites Bombes" / "P'tites"),
    transliteracja umlautow ("Räuberella" / "Raeuberella"), znak ®/™ po marce
    oraz poprzedzajace "von"/"by". Dopasowanie tylko calych slow - marka
    "Lego" nie moze uciac poczatku "Legolas". Kilka nazw jednej marki
    (linie produktowe z D.BRAND_ID_TO_NAME, "Muchachomalo|Chicamala") -
    pasuje dowolna z nich."""
    cores = [c for c in (_brand_core(n) for n in brand_name.split("|")) if c]
    if not cores:
        return None
    core = "|".join(cores)
    return re.compile(
        rf"(?:\b(?:von|by)\s+)?(?<!{_BRAND_WORD_CHAR})(?:{core})(?!{_BRAND_WORD_CHAR})\s*[®™©]?",
        re.IGNORECASE)


def _brand_core(brand_name: str) -> str | None:
    """Wzorzec (bez kotwic) jednej nazwy marki - patrz _brand_pattern."""
    chunks = re.findall(rf"{_BRAND_WORD_CHAR}+|[{_BRAND_APOSTROPHES}]", brand_name)
    if not chunks:
        return None
    parts = []
    for chunk in chunks:
        if chunk in _BRAND_APOSTROPHES:
            parts.append(f"[{_BRAND_APOSTROPHES}]?")
            continue
        # "BlueStar" / "Chic4Baby" - czlony CamelCase i litery/cyfry tez
        # moga byc w tytule rozdzielone ("Blue Star", "Chic 4 Baby")
        for sub in re.split(r"(?<=[a-zäöüß])(?=[A-ZÄÖÜ])|(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", chunk):
            piece = "".join(_BRAND_CHAR_VARIANTS.get(ch.lower(), re.escape(ch)) for ch in sub)
            if parts and not parts[-1].endswith("]?"):
                parts.append(r"[\s\-_.&+]*")
            parts.append(piece)
    return "".join(parts)


def _brand_name_for_text(row: dict) -> str:
    """Nazwa marki do szukania w tekscie - brandName/Marke, a gdy to numeryczne
    ID, nazwa z listy marek (D.brand_name_for_id; pusta, gdy ID nieznane).
    Marki bedace zwyklym slowem tytulu (D.GENERIC_BRAND_WORDS) pomijamy."""
    brand = str(row.get("brandName") or row.get("Marke") or "").strip()
    if brand.isdigit():
        brand = D.brand_name_for_id(brand)
    if brand.lower() in D.GENERIC_BRAND_WORDS:
        return ""
    words = brand.split()
    if (len(words) >= 2 and "|" not in brand and len(words[0]) >= 2
            and words[0].lower() not in D.GENERIC_BRAND_WORDS
            and all(w.lower().strip("&.,") in D.BRAND_GENERIC_SUFFIX_WORDS or w == "&"
                    for w in words[1:])):
        # "XQ FOOTWEAR" zapisane w tytule jako "XQ" (patrz BRAND_GENERIC_SUFFIX_WORDS)
        return f"{brand}|{words[0]}"
    return brand


def _strip_brand_name(text: str, brand_name: str, allow_empty: bool = False) -> tuple[str, bool]:
    """Usuwa nazwe marki (brandName/Marke) z tekstu, gdziekolwiek wystepuje
    (patrz _brand_pattern) - marka nie powinna byc czescia tytulu. Tak jak
    modelName_text, brandName w danych bywa blednie ustawione na caly tytul -
    w takim przypadku nie probujemy nic usuwac (patrz _looks_like_short_value).
    Numeryczne ID marki (np. '22853') nie da sie dopasowac do tekstu.
    allow_empty - text to tylko fragment tytulu obok modelu (patrz
    strip_title_junk), wiec moze zostac pusty: "NOTIQUE " przed "Riviera"."""
    brand_name = (brand_name or "").strip()
    if (not brand_name or not text or brand_name.isdigit()
            or not _looks_like_short_value(brand_name)):
        return text, False
    pattern = _brand_pattern(brand_name)
    if pattern is None or not pattern.search(text):
        return text, False
    new_text = pattern.sub(" ", text)
    new_text = re.sub(r"^[\s\-–—:,|/]+", "", new_text)
    new_text = re.sub(r"[\s\-–—:,|/]+$", "", new_text)
    new_text = re.sub(r"\s+([,:])", r"\1", new_text)
    new_text = re.sub(r"\s{2,}", " ", new_text).strip()
    if not new_text and not allow_empty:
        return text, False
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
    keep_gender: bool = False,
    keep_mit: bool = False,
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
    'mit X' (patrz _strip_trailing_mit_clause). keep_gender/keep_mit - patrz
    title_rules.TitleProfile (plec/'mit X' wymagane w tytule danej grupy)."""
    if not title:
        return title, False
    model_name = (model_name or "").strip()
    if not _looks_like_short_value(model_name):
        model_name = ""

    def _clean_segment(segment: str, beside_model: bool = False) -> tuple[str, bool]:
        t, ch1 = (segment, False) if keep_gender else _strip_gender_words(segment)
        t, ch2 = _strip_brand_name(t, brand_name, allow_empty=beside_model)
        t, ch3 = _strip_size_tokens(t)
        t, ch4 = (t, False) if keep_mit else _strip_trailing_mit_clause(t)
        return t, ch1 or ch2 or ch3 or ch4

    span = _find_model_span(title, model_name) if model_name else None
    if span and keep_mit:
        # fragment po modelu bywa wymaganym 'mit X' ("Halskette Luna mit
        # Anhänger") - nie obcinamy go, ale model nadal chronimy przed
        # czyszczeniem (np. kod rozmiaru w nazwie modelu "LEI Pearl S-M")
        before, model_actual, after = title[:span[0]], title[span[0]:span[1]], title[span[1]:]
        before_fixed, ch1 = _clean_segment(before, beside_model=True)
        after_fixed, ch2 = _clean_segment(after, beside_model=True)
        if not (ch1 or ch2):
            return title, False
        new_title = " ".join(p for p in (before_fixed.strip(), model_actual.strip(), after_fixed.strip()) if p)
        return new_title, new_title != title
    if span:
        before, model_actual, after = title[:span[0]], title[span[0]:span[1]], title[span[1]:]
        before_fixed, _ = _clean_segment(before, beside_model=True)
        after_fixed, _ = _clean_segment(after, beside_model=True)

        use_after_as_type = False
        if category_tokens:
            score_before = _phrase_match_score(before_fixed, category_tokens)
            score_after = _phrase_match_score(after_fixed, category_tokens)
            use_after_as_type = score_after > score_before

        kept_type = after_fixed if use_after_as_type else before_fixed
        parts = [kept_type.strip(), model_actual.strip()]
        new_title = " ".join(p for p in parts if p)
        return new_title, new_title != title

    # model spoza tytulu i tak zostanie dopisany (ensure_model_present), wiec
    # tytul bedacy sama marka ("NOTIQUE") moze sie skurczyc do samego modelu
    new_title, changed = _clean_segment(title, beside_model=bool(model_name))
    if not new_title.strip() and model_name:
        new_title, changed = model_name, True
    return new_title, changed


def _normalize_for_compare(text: str) -> str:
    """Normalizuje spacje/lacznik, zeby porownac np. 'Sherpa Jacke' (w tytule)
    z 'Sherpa-Jacke' (modelName_text) jako ten sam tekst - w danych zdarzaja
    sie oba separatory dla tego samego modelu."""
    return re.sub(r"[\s\-]+", " ", text).strip().lower()


def _compact(text: str) -> str:
    """Tylko litery/cyfry, lowercase - 'Spider Ball' == 'Spiderball'."""
    return re.sub(r"[\W_]+", "", text).lower()


def _strip_model_color_tail(model_name: str, color_value: str = "") -> str:
    """'Spider Ball - Green' -> 'Spider Ball': koncowka modelu po myslniku
    bedaca kolorem (kolor i tak trafia do tytulu jako 'in Kolor')."""
    m = re.match(r"^(.*\S)\s*[-–—/]\s*([^-–—/]+)$", model_name)
    if not m:
        return model_name
    tail = m.group(2).strip().lower()
    colors = set(D.COLORS_EN_TO_DE) | {v.lower() for v in D.COLORS_EN_TO_DE.values()}
    if color_value:
        colors.add(color_value.strip().lower())
    return m.group(1) if tail in colors else model_name


def ensure_product_type(title: str, product_type: str, category_tokens: set[str] | None = None,
                        model_name: str = "") -> tuple[str, bool]:
    """Tytul bez typu produktu ("Hawk", gdy manufacturer_product_type_text_de =
    "Hawk Stunt Scooter") - dopisuje na poczatku brakujaca czesc typu ("Stunt
    Scooter Hawk"). Tylko gdy pole typu dzieli z tytulem slowo (czyli ma forme
    "model + typ"), tytul jest krotki (sam model, patrz _looks_like_short_value)
    i nie zawiera ani reszty typu, ani slowa z etykiety
    kategorii - inaczej typ w tytule juz jest (np. "Hantel 2x 2kg Set" przy
    typie "Hantelset").

    Drugi przypadek: pole typu to sam typ ("Wochenplaner"), a tytul to
    DOKLADNIE sam model ("Riviera" == modelName_text) - dopisujemy caly typ
    na poczatku ("Wochenplaner Riviera"). Rownosc tytulu z modelem to
    najmocniejszy sygnal, ze w tytule nie ma nic poza modelem."""
    if not title or not product_type:
        return title, False
    core_words = re.split(r"\s+in\s+", title.strip(), maxsplit=1)[0].split()
    if len(core_words) > _MAX_SHORT_VALUE_WORDS:
        # dlugi tytul to juz opis produktu, nie sam model
        return title, False
    title_tokens = {_compact(w) for w in re.split(r"[\s\-/]+", title) if _compact(w)}
    title_compact = _compact(title)
    pt_words = [w for w in re.split(r"[\s/]+", product_type.strip()) if _compact(w)]
    if not title_tokens or not pt_words or len(pt_words) > _MAX_SHORT_VALUE_WORDS:
        return title, False

    def _in_title(word: str) -> bool:
        c = _compact(word)
        return c in title_tokens or c in title_compact  # "3er-Set" vs "3er Set"

    # pole ma forme "model + typ": slowa modelu na poczatku, typ za nimi
    n_model = 0
    while n_model < len(pt_words) and _in_title(pt_words[n_model]):
        n_model += 1
    remainder = pt_words[n_model:]
    if n_model == 0:
        # pole to sam typ - tylko gdy tytul (bez 'in Kolor') to dokladnie model
        core = " ".join(core_words)
        if not model_name or _compact(core) != _compact(model_name):
            return title, False
    if not remainder or any(_in_title(w) for w in remainder):
        return title, False
    # typ to rzeczowniki ("Stunt Scooter", "Kinderroller") - nie "mit Bommel",
    # "blau-weiß", "Kinder"
    colors = set(D.COLORS_EN_TO_DE) | {v.lower() for v in D.COLORS_EN_TO_DE.values()}
    for w in remainder:
        if not w[0].isupper() or w.lower() in TITLE_GENDER_WORDS or w.lower() in colors:
            return title, False
    if category_tokens and _phrase_match_score(title, category_tokens):
        return title, False
    return f"{' '.join(remainder)} {title.strip()}", True


def ensure_model_present(title: str, model_name: str, color_value: str = "") -> tuple[str, bool]:
    """Jesli modelName_text nie wystepuje w tytule (nawet w formie z innym
    separatorem czlonow - patrz _normalize_for_compare) - dopisuje go na koncu
    tytulu. Wywolywane PRZED ensure_in_before_color w process_product(), zeby
    kolejnosc koncowa byla 'Typ produktu + Model + in Kolor'. Nie dopisuje nic,
    gdy model_name nie wyglada na prawdziwy, krotki model (patrz
    _looks_like_short_value) - inaczej blednie zdublowalibysmy caly tytul."""
    model_name = (model_name or "").strip()
    if not model_name or not title or not _looks_like_short_value(model_name):
        return title, False
    if model_name.isdigit():
        # sam numer artykulu (Lucardi: modelName_text='1062348' przy
        # variant_group_code 'P-1062348') - w poprawnych tytulach go nie ma
        return title, False
    model_name = _strip_model_color_tail(model_name, color_value)
    if _normalize_for_compare(model_name) in _normalize_for_compare(title):
        return title, False
    if _compact(model_name) and _compact(model_name) in _compact(title):
        # inny podzial na slowa ("Spider Ball" vs tytul "Spiderball Set")
        return title, False
    model_core, _ = _strip_size_tokens(model_name)
    if model_core and _normalize_for_compare(model_core) in _normalize_for_compare(title):
        # model rozni sie od tytulu tylko rozmiarem ("LEI Charm S-M" vs tytul
        # "LEI Charm M-L") - rozmiar i tak nie nalezy do tytulu
        return title, False
    new_title = f"{title.rstrip()} {model_name}"
    return new_title, True


def apply_title_fixes_excluding_model(text: str, model_name: str,
                                      color_pattern: bool = True,
                                      protected: tuple[str, ...] = ()) -> tuple[str, bool]:
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
        before_fixed, ch1 = fix_double_spaces_and_grammar(before, color_pattern)
        before_fixed, ch1b = apply_translation_fixes(before_fixed, protected)
        after_fixed, ch2 = fix_double_spaces_and_grammar(after, color_pattern)
        after_fixed, ch2b = apply_translation_fixes(after_fixed, protected)
        new_text = before_fixed + model_actual + after_fixed
        return new_text, ch1 or ch1b or ch2 or ch2b
    new_text, ch1 = fix_double_spaces_and_grammar(text, color_pattern)
    new_text, ch2 = apply_translation_fixes(new_text, protected)
    return new_text, ch1 or ch2



def fix_color_word(value: str, prose: bool = False) -> tuple[str, bool, str | None]:
    """prose=True (Long Description) - pomija slowa, ktore sa tez zwyklymi
    niemieckimi slowami (D.COLORS_EN_AMBIGUOUS_IN_PROSE, np. 'Rose')."""
    if not value:
        return value, False, None
    changed_any = False

    def _repl(m: re.Match) -> str:
        nonlocal changed_any
        word = m.group(0)
        if prose and word.lower() in D.COLORS_EN_AMBIGUOUS_IN_PROSE:
            return word
        de = D.COLORS_EN_TO_DE.get(word.lower())
        if de and de != word:
            changed_any = True
            return de
        return word

    # dopasowanie slow niezaleznie od otaczajacej interpunkcji (przecinki, kropki
    # itp.) - wazne zwlaszcza w tekscie prozy (Long Description), nie tylko
    # w krotkich, "czystych" polach jak tytul/kolor producenta
    new_value = re.sub(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", _repl, value)
    if changed_any:
        # "Schwarz Black" -> "Schwarz", nie "Schwarz Schwarz"
        new_value = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", new_value)
    note = "Translated English color name to German." if changed_any else None
    return new_value, changed_any, note


def _color_word_to_de(word: str) -> str | None:
    lw = word.lower()
    return D.COLORS_EN_TO_DE.get(lw) or D.COLORS_FOREIGN_TO_DE.get(lw)


_GERMAN_COLOR_WORDS = None


def _is_german_color(word: str) -> bool:
    global _GERMAN_COLOR_WORDS
    if _GERMAN_COLOR_WORDS is None:
        _GERMAN_COLOR_WORDS = ({v.lower() for v in D.COLORS_EN_TO_DE.values()}
                               | {v.lower() for v in D.COLORS_FOREIGN_TO_DE.values()})
    return word.lower() in _GERMAN_COLOR_WORDS


def _shade_compound(shade: str, base: str) -> str:
    """"Dunkel" + "Blau" -> "Dunkelblau"; "Hell" + "Pink" -> "Hellrosa"."""
    if base.lower() == "pink":
        base = "rosa"
    return shade + base.lower()


def fix_manufacturer_color(value: str) -> tuple[str, bool, str | None]:
    """color_manufacturer_text zawsze po niemiecku: tlumaczy nazwy kolorow z
    EN/NL/FR/IT/ES/PL/DA/SV (D.COLORS_EN_TO_DE + D.COLORS_FOREIGN_TO_DE),
    frazy ("Off White") i odcienie, takze w zlozeniach ("Donker Grijs",
    "Donkerbruin", "Dark Blue" -> "Dunkelgrau", "Dunkelbraun", "Dunkelblau").
    Slowa spoza slownikow (motywy: "Flamingo", "Stars") zostaja bez zmian."""
    if not value:
        return value, False, None
    new = value
    for phrase, de in D.COLOR_PHRASES_TO_DE.items():
        new = re.sub(rf"(?<![\w]){re.escape(phrase)}(?![\w])", de, new, flags=re.IGNORECASE)

    parts = re.split(r"([A-Za-zÀ-ÖØ-öø-ÿĄ-žß]+)", new)  # [sep, slowo, sep, slowo, ..., sep]
    out = []
    i = 0
    while i < len(parts):
        tok = parts[i]
        if i % 2 == 0:
            out.append(tok)
            i += 1
            continue
        shade = D.COLOR_SHADE_PREFIXES_TO_DE.get(tok.lower())
        # odcien jako osobne slowo: "Donker Grijs" -> "Dunkelgrau"
        if shade and i + 2 < len(parts) and parts[i + 1].strip() == "":
            nxt = parts[i + 2]
            base = _color_word_to_de(nxt) or (nxt if _is_german_color(nxt) else None)
            if base:
                out.append(_shade_compound(shade, base))
                i += 3
                continue
        de = _color_word_to_de(tok)
        if de and i + 2 < len(parts) and parts[i + 1].strip() == "":
            # odcien po kolorze: "Bleu foncé" -> "Dunkelblau"
            post = D.COLOR_SHADE_SUFFIXES_TO_DE.get(parts[i + 2].lower())
            if post:
                out.append(_shade_compound(post, de))
                i += 3
                continue
        if de is None:
            # odcien w zlozeniu: "Donkerbruin", "Lichtgrijs"
            for prefix, shade_de in D.COLOR_SHADE_PREFIXES_TO_DE.items():
                rest = tok[len(prefix):]
                if tok.lower().startswith(prefix) and len(rest) >= 3:
                    base = _color_word_to_de(rest) or (rest if _is_german_color(rest) else None)
                    if base:
                        de = _shade_compound(shade_de, base)
                        break
        out.append(de if de else tok)
        i += 1
    new = "".join(out)
    new = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", new)  # "Schwarz Black" -> "Schwarz"
    if new == value:
        return value, False, None
    return new, True, f"Translated manufacturer color to German: '{value}' -> '{new}'."


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
    note = "Fixed material composition format/language." if changed else None
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
                problems.append(f"'{part}' -> should be '{proper}' (letter case)")
            else:
                problems.append(f"'{part}' is not in ReferenceData")
    if not problems:
        return None
    hint = ""
    if len(allowed) <= 40:
        hint = f" Allowed: {sorted(allowed)}."
    return f"Field {field_code}: " + "; ".join(problems) + "." + hint


# --- colors (Limango Color) z koloru producenta -----------------------------------

def _manufacturer_word_to_code(word: str) -> str | None:
    w = word.lower()
    code = D.MANUFACTURER_COLOR_TO_CODE.get(w)
    if code:
        return code
    for prefix in D.MANUFACTURER_COLOR_SHADE_PREFIXES:
        if w.startswith(prefix) and len(w) > len(prefix):
            return D.MANUFACTURER_COLOR_TO_CODE.get(w[len(prefix):].lstrip("-"))
    return None


def derive_limango_color(manufacturer_color: str,
                         allowed: set[str] | None) -> tuple[str | None, list[str]]:
    """Wartosc dla pustego pola colors na podstawie color_manufacturer_text.
    Zwraca (wartosc albo None, znalezione kody). Jeden kolor bazowy
    ('Dunkelgrau' -> grau) albo okreslenie wielokolorowe ('Mehrfarbig -
    Flamingo', 'Bicolor' -> bunt). Przy kilku kolorach bierzemy pierwszy - tak
    robia dostawcy w poprawionych eksportach ('Gold Weiß' -> gold: metal, potem
    kamien); wywolujacy oznacza to do weryfikacji. Slowa nie bedace kolorem
    (motywy, 'Matt') sa pomijane. Format wartosci wg szablonu: etykieta z
    ReferenceData, gdy plik ja ma, inaczej code (COLORS_VALID_CODES)."""
    codes: list[str] = []
    # "GoldRosegold" -> "Gold Rosegold"
    text = re.sub(r"(?<=[a-zäöüß])(?=[A-ZÄÖÜ])", " ", manufacturer_color or "")
    for word in re.findall(r"[^\W\d_]+", text):
        code = _manufacturer_word_to_code(word)
        if code and code not in codes:
            codes.append(code)
    if "multicolored" in codes:
        chosen = "multicolored"
    elif codes:
        chosen = codes[0]
    else:
        return None, codes
    if allowed:
        lower_map = {a.lower(): a for a in allowed}
        for label in D.COLOR_CODE_DE_LABELS.get(chosen, []) + [chosen]:
            if label in lower_map:
                return lower_map[label], codes
        return None, codes
    return (chosen if chosen in D.COLORS_VALID_CODES else None), codes


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
                    f"Titles in the variant group have no common base ('{title}') - "
                    f"needs manual review.", "manual_review"))
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
                f"Aligned title with its variant group (removed '{removed}'): "
                f"'{title}' -> '{new_title}'.", "auto_fixed"))
    return issues


def protected_model_names(products: list) -> tuple[str, ...]:
    """Nazwy modeli z calego pliku zawierajace angielskie slowo koloru ("Blue
    Lagoon", "Velvet Rose") - nie tlumaczymy ich w tytulach ani opisach."""
    names = set()
    for row in products:
        model = str(row.get("modelName_text") or "").strip()
        if (len(model) >= 3 and _looks_like_short_value(model)
                and any(w.lower() in D.COLORS_EN_TO_DE for w in re.findall(r"[A-Za-z]+", model))):
            names.add(model)
    return tuple(names)


def generic_model_names(products: list, min_groups: int = 3) -> frozenset[str]:
    """modelName_text wspolny dla wielu roznych grup wariantow to nie model,
    tylko ogolny typ/kategoria - czesto w obcym jezyku ("Compressiesokken",
    "Sloffen kids unisex - HW8Q" w eksporcie Morethansocks przy kilkunastu
    roznych produktach). Takiego "modelu" nie dopisujemy do tytulu."""
    groups: dict[str, set[str]] = {}
    for row in products:
        model = str(row.get("modelName_text") or "").strip()
        group = str(row.get("variant_group_code") or row.get("Variant Group") or "").strip()
        if model and group:
            groups.setdefault(model, set()).add(group)
    return frozenset(m for m, g in groups.items() if len(g) >= min_groups)


def tidy_title_separators(text: str) -> str:
    """Sprzatanie po usunieciu czlonow tytulu: "6er-Pack - - Vorteilspack" ->
    "6er-Pack - Vorteilspack" oraz czlon po myslniku powtarzajacy wczesniejsze
    slowo ("Gartenclogs - gefüttert - Gartenclogs in Blau" -> "Gartenclogs -
    gefüttert in Blau")."""
    if not text:
        return text
    t = re.sub(r"\s+([-–—])(?:\s+[-–—])+(?=\s)", r" \1", text)
    t = re.sub(r"^\s*[-–—]\s+", "", t)
    t = re.sub(r"\s+[-–—]\s*$", "", t)
    t = re.sub(r"\s+[-–—]\s+(?=in\s)", " ", t)  # "Gartenclogs - in Blau"
    parts = re.split(r"(\s+[-–—]\s+)", t)
    if len(parts) >= 3:
        seen_words = {w.lower() for w in re.findall(r"[\wÄÖÜäöüß]+", parts[0])}
        kept = [parts[0]]
        for k in range(2, len(parts), 2):
            seg, sep = parts[k], parts[k - 1]
            m = re.match(r"^(.*?)(\s+in\s+.*)?$", seg)
            body, tail = m.group(1), m.group(2) or ""
            if body.strip().lower() in seen_words:
                kept[-1] = kept[-1] + tail
                continue
            seen_words |= {w.lower() for w in re.findall(r"[\wÄÖÜäöüß]+", body)}
            kept += [sep, seg]
        t = "".join(kept)
    return re.sub(r"\s{2,}", " ", t).strip()


def process_products(products: list, category_tree: CategoryTree,
                     reference_values: dict | None = None) -> list[tuple[OrderedDict, list[dict]]]:
    """Przetwarza caly plik: reguly wymagajace widoku grupy wariantow (prefiks
    koloru, spojnosc tytulow) + process_product dla kazdego wiersza."""
    prefixes = variant_group_color_prefixes(products, reference_values)
    model_names = protected_model_names(products)
    generic_models = generic_model_names(products)
    results = [process_product(row, category_tree, color_prefix=prefixes[i],
                               reference_values=reference_values,
                               protected_names=model_names,
                               generic_models=generic_models)
               for i, row in enumerate(products)]
    group_issues = harmonize_variant_group_titles([r[0] for r in results],
                                                  [p is not None for p in prefixes])
    for i, extra in group_issues.items():
        results[i][1].extend(extra)
    return results


# --- glowna funkcja per-produkt --------------------------------------------------

def process_product(row: OrderedDict, category_tree: CategoryTree, color_prefix: str | None = None,
                    reference_values: dict | None = None,
                    protected_names: tuple[str, ...] = (),
                    generic_models: frozenset[str] = frozenset()) -> tuple[OrderedDict, list[dict]]:
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
                                 f"Could not resolve category '{raw_cat}' in all_categories.json.",
                                 "manual_review"))
        else:
            title_field = _first_present(new_row, TITLE_CODE_CANDIDATES)
            title_text = new_row.get(title_field, "") if title_field else ""
            desc_field = _first_present(new_row, DESC_CODE_CANDIDATES)
            desc_text = new_row.get(desc_field, "") if desc_field else ""
            check_text, _ = fix_broken_umlauts(f"{title_text} {desc_text}")

            # Dedykowana, precyzyjna regula: wzorzec "X tlg./er-Set" w tytule a
            # kategoria "pojedyncza" -> sprawdz czy istnieje kategoria-siostra "*-Sets"
            is_set_title = bool(re.search(r"\b\d+\s*(-|\s)?tlg\.?|\b\d+\s*er[- ]?set\b",
                                           str(title_text), re.IGNORECASE))
            set_sibling = category_tree.find_set_sibling(resolved_code) if is_set_title else None
            if set_sibling:
                issues.append(issue(
                    cat_code_field, "CATEGORY_SET_MISMATCH",
                    (f"The title indicates a multi-piece set ('{title_text}'), but the product "
                     f"is in category '{category_tree.path_de(resolved_code)}' (code {resolved_code}) "
                     f"instead of the dedicated set category "
                     f"'{category_tree.path_de(set_sibling)}' (code {set_sibling})."),
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
                        (f"The product content ('{title_text}') suggests category "
                         f"'{best_path}' (code {best_code}), but the product is assigned to "
                         f"'{category_tree.path_de(resolved_code)}' (code {resolved_code}). "
                         f"The suggested change needs confirmation."),
                        "manual_review"))

    # --- kolor producenta: EN -> DE (przed tytulem, zeby uzyc poprawnej wartosci
    # przy ustawianiu 'in' przed kolorem w tytule) ---------------------------------
    color_field = _first_present(new_row, COLOR_MANUFACTURER_CANDIDATES)
    if color_field and new_row.get(color_field):
        new_val, changed, note = fix_manufacturer_color(str(new_row[color_field]))
        if changed:
            new_row[color_field] = new_val
            issues.append(issue(color_field, "COLOR_LANGUAGE_FIXED", note, "auto_fixed"))

    # --- colors (Limango Color) puste -> z koloru producenta, przed usunieciem
    # prefiksu grupy wariantow ('Mehrfarbig - Flamingo' to nadal 'bunt') --------
    colors_field = _first_present(new_row, COLORS_FIELD_CANDIDATES)
    manufacturer_color = str(new_row.get(color_field) or "").strip() if color_field else ""
    if colors_field and not str(new_row.get(colors_field) or "").strip() and manufacturer_color:
        refs = reference_values or {}
        allowed = refs.get(colors_field) or refs.get("colors")
        derived, found = derive_limango_color(manufacturer_color, allowed)
        if derived and len(found) > 1 and "multicolored" not in found:
            new_row[colors_field] = derived
            issues.append(issue(colors_field, "COLOR_FILLED_FROM_MANUFACTURER",
                                 f"Filled empty field {colors_field} with the first manufacturer "
                                 f"color '{manufacturer_color}' -> '{derived}' (colors: "
                                 f"{', '.join(found)}) - check that it is the dominant color.",
                                 "manual_review"))
        elif derived:
            new_row[colors_field] = derived
            issues.append(issue(colors_field, "COLOR_FILLED_FROM_MANUFACTURER",
                                 f"Filled empty field {colors_field} from the manufacturer "
                                 f"color '{manufacturer_color}' -> '{derived}'.",
                                 "auto_fixed"))
        else:
            issues.append(issue(colors_field, "COLOR_NOT_DERIVED",
                                 f"Field {colors_field} is empty; no color recognized in the manufacturer "
                                 f"color '{manufacturer_color}' - fill it in manually.",
                                 "manual_review"))

    # --- kolor producenta: usuniecie wspolnego prefiksu grupy wariantow
    # ("Mehrfarbig - Flamingo" -> "Flamingo"), patrz variant_group_color_prefixes -
    prefix_word = None
    if color_prefix and color_field and str(new_row.get(color_field) or "").startswith(color_prefix):
        old_val = str(new_row[color_field])
        motif = old_val[len(color_prefix):].strip()
        new_row[color_field] = motif
        prefix_word = _COLOR_PREFIX_SEPARATOR_RE.sub("", color_prefix).strip()
        issues.append(issue(color_field, "COLOR_PREFIX_REMOVED",
                             f"Removed the common prefix '{prefix_word}' from the manufacturer color: "
                             f"'{old_val}' -> '{motif}' (the motif distinguishes the variants).",
                             "auto_fixed"))

    # --- tytul: struktura zalezna od grupy kategorii (title_rules.TitleProfile,
    # zrodlo: Product_categories_guidelines) - domyslnie "Typ produktu + Model +
    # in Kolor" (zalozenie 1d w README.md). Usuwamy smieci (plec, stary opis
    # wzoru dublujacy kolor), naprawiamy jezyk/formatowanie, upewniamy sie ze
    # model i (gdy grupa go wymaga) kolor sa obecne (poza nazwa modelu, ktora
    # nigdy nie jest modyfikowana) ---------------------------------------------
    title_field = _first_present(new_row, TITLE_CODE_CANDIDATES)
    title_group = FR.detect_group(category_tree.path_de(resolved_code) if resolved_code else None)
    profile = TR.profile_for(title_group)
    if title_field and new_row.get(title_field) and not profile.restructure:
        # Literatura: "Typ - Tytul" - tytul ksiazki nie moze byc tlumaczony,
        # przycinany ani uzupelniany o kolor; tylko uciete umlauty i spacje
        text = str(new_row[title_field])
        fixed, ch1 = fix_broken_umlauts(text)
        fixed, _ = _strip_brand_name(fixed, _brand_name_for_text(new_row))
        fixed = re.sub(r"\s{2,}", " ", fixed).strip()
        if ch1 or fixed != text:
            new_row[title_field] = fixed
            issues.append(issue(title_field, "TITLE_FORMAT_FIXED",
                                 f"Fixed title formatting: '{text}' -> '{fixed}'.",
                                 "auto_fixed"))
    elif title_field and new_row.get(title_field):
        text = str(new_row[title_field])
        model_name = str(new_row.get("modelName_text", "") or "")
        title_compact = _compact(text)
        if (_compact(model_name) not in title_compact
                and (model_name.strip() in generic_models or _strip_gender_words(model_name)[1])):
            # ogolny typ wspolny dla wielu produktow albo "model" ze slowem
            # plci ("Klompen dames") - patrz generic_model_names
            model_name = ""
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
        brand_name = _brand_name_for_text(new_row)
        if model_name.strip() and brand_name.strip():
            # marka wewnatrz modelName_text ("Geographical Norway G-ROSE") -
            # model jest chroniony przed czyszczeniem i dopisywany, gdy go
            # brak (ensure_model_present), wiec bez tego marka wracalaby do
            # tytulu; model rowny samej marce nie jest modelem
            brand_re = _brand_pattern(brand_name.strip())
            if brand_re and brand_re.fullmatch(model_name.strip()):
                model_name = ""
            elif _looks_like_short_value(model_name):
                # dlugi modelName_text to opis, nie model - pomijany dalej
                # (patrz _looks_like_short_value); wyciecie marki nie moze go
                # skrocic do "modelu" doklejanego do tytulu
                model_name, _ = _strip_brand_name(model_name, brand_name)
        category_tokens = _category_type_tokens(category_tree, resolved_code)

        # koncowki wymagane przez wytyczne (wiek, ilosc, EEK, "Gr. 2") oraz
        # wymiary odcinamy przed czyszczeniem i doklejamy z powrotem na koncu
        core, suffixes = TR.split_suffixes(text, profile)
        dim_parts = "".join(t for k, t in suffixes if k == "dimensions")
        other_parts = "".join(t for k, t in suffixes if k != "dimensions")

        # Wymiary: wzorzec "... – (B)W x (H)H x (T)D cm" (przyklad "Wandspiegel
        # 3039 in Walnuss – (B)46 x (H)46 x (T)6 cm"); wg wytycznych tylko dla
        # produktow onesize i tylko w grupach, ktorych struktura tytulu je zawiera
        dim_suffix = build_dimension_suffix(new_row)
        onesize = TR.is_onesize(new_row.get("sizes") or new_row.get("Größe"))
        dims_removed = False
        if profile.dimensions == "none" or (profile.dimensions == "onesize" and onesize is False):
            dims_out = ""
            dims_removed = bool(dim_parts)
        elif (profile.dimensions == "onesize" and dim_suffix
              and not re.search(r"\([A-Za-z]\)", dim_parts)):
            # brak wymiarow lub stary format bez (B)/(H)/(T) -> z pol wymiarow
            dims_out = f" – {dim_suffix}"
        elif profile.dimensions == "onesize" and dim_parts:
            # ujednolicenie separatora ("-120x120 cm", "-  75x100 cm" -> " – ...")
            dims_out = " – " + re.sub(r"\s{2,}", " ", re.sub(r"^[\s,\-–—]+", "", dim_parts))
        else:
            dims_out = dim_parts

        base = core
        if prefix_word:
            base, _ = _strip_word(base, prefix_word)

        base, _ = _translate_title_nl_words(base)
        base, _ = _strip_generic_category_segments(base)
        text4, _ = strip_title_junk(base, model_name, brand_name, category_tokens,
                                    keep_gender=profile.keep_gender, keep_mit=profile.keep_mit)
        use_color = profile.color is True or (
            profile.color == "keep" and bool(re.search(r"\bi[nm]\s+[A-ZÄÖÜ]", core)))
        text4, _ = apply_title_fixes_excluding_model(text4, model_name, use_color, protected_names)
        color_for_model = str(new_row.get(color_field, "") or "") if color_field else ""
        text4, _ = ensure_model_present(text4, model_name, color_for_model)
        # tytul bedacy samym modelem ("Hawk") - typ z manufacturer_product_type_text_de
        text4, _ = ensure_product_type(
            text4, str(new_row.get("manufacturer_product_type_text_de") or ""), category_tokens,
            model_name)

        if use_color:
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
                    text4, _ = _strip_bare_color_word_fragments(text4, color_value)
            text4, _ = ensure_in_before_color(text4, color_value)

        format_changed = text4 != core or (bool(dim_parts) and bool(dims_out) and dims_out != dim_parts)
        text4 = tidy_title_separators(text4)
        final_title = f"{text4}{other_parts}{dims_out}"
        if final_title != text:
            new_row[title_field] = final_title
        if format_changed:
            issues.append(issue(title_field, "TITLE_FORMAT_FIXED",
                                 f"Fixed title formatting/language: '{text}' -> '{final_title}'.",
                                 "auto_fixed"))
        if dims_removed:
            reason = ("the product is not onesize (dimensions belong in the size field)"
                      if profile.dimensions == "onesize"
                      else "the title structure of this category has no dimensions")
            issues.append(issue(title_field, "TITLE_DIMENSIONS_REMOVED",
                                 f"Removed dimensions from the title - {reason}: '{text}' -> '{final_title}'.",
                                 "auto_fixed"))
        if dims_out and not dim_parts:
            issues.append(issue(title_field, "TITLE_DIMENSIONS_ADDED",
                                 f"Added dimensions to the title from width/height/depth_numeric: '{final_title}'.",
                                 "auto_fixed"))
        if profile.color is True and " in " not in text4 and " im " not in text4:
            issues.append(issue(title_field, "TITLE_PATTERN_MISSING",
                                 "The title does not follow the 'Type (+Model) in Color' pattern - needs manual review.",
                                 "manual_review"))

    # --- Long Description: te same poprawki jezykowe (EN->DE slowa koloru/materialu) -
    desc_field = _first_present(new_row, DESC_CODE_CANDIDATES)
    if desc_field and new_row.get(desc_field):
        desc_text = str(new_row[desc_field])
        desc_fixed, desc_changed = apply_translation_fixes(desc_text, protected_names, prose=True)
        if desc_changed:
            new_row[desc_field] = desc_fixed
            issues.append(issue(desc_field, "DESCRIPTION_LANGUAGE_FIXED",
                                 "Fixed language/spelling in Long Description (English "
                                 "color/material words, broken special characters).",
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
                                 f"Model field '{model_val}' seems to contain a color/size - needs manual review.",
                                 "manual_review"))

    # --- brandName: wykrycie formatu (numeryczne ID vs tekst) - tylko raportowanie -
    brand_field = "brandName" if "brandName" in new_row else ("Marke" if "Marke" in new_row else None)
    if brand_field and new_row.get(brand_field):
        brand_val = str(new_row[brand_field]).strip()
        if brand_val.isdigit() and not D.brand_name_for_id(brand_val):
            issues.append(issue(brand_field, "BRAND_IS_NUMERIC_ID",
                                 f"Brand given as a numeric ID ({brand_val}) - this code is not on the brand list (data/brands.csv).",
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
                                     f"Unknown tokens {unknown} in field {field_code} - not in the known dictionary.",
                                     "manual_review"))

    return new_row, issues
