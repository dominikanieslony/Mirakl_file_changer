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

TITLE_CODE_CANDIDATES = ["ShortDescription_de", "Produktname"]
DESC_CODE_CANDIDATES = ["LongDescription_de", "Langbeschreibung"]
CATEGORY_CODE_CANDIDATES = ["CATEGORY", "Kategorie"]
COLOR_MANUFACTURER_CANDIDATES = ["color_manufacturer_text", "Herstellerfarbbezeichnung"]
MATERIAL_CODE_CANDIDATES = ["materialComposition_de", "Materialzusammensetzung"]
GENDER_CODE_CANDIDATES = ["genders", "Geschlecht"]
AGE_CODE_CANDIDATES = ["ages", "Altersgruppe"]
COLORS_FIELD_CANDIDATES = ["colors", "Limango Farbe"]


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
    # " im " przed kolorem powinno byc " in " zgodnie ze wzorcem "Typ (+Model) in Farbe"
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


# niemieckie przymiotniki koloru odmieniaja sie przez rodzaj/przypadek (np.
# "schwarz" -> "schwarzes/schwarzem/schwarzen/schwarze") - dopuszczamy typowe
# koncowki, zeby wykryc kolor nawet gdy w tytule opisuje inny rzeczownik (np.
# wzor/material: "mit schwarzem Schachbrettmuster"), a nie sam produkt.
_COLOR_INFLECTION_SUFFIX = r"(?:e[mnrs]?)?"


def ensure_in_before_color(title: str, color_value: str) -> tuple[str, bool]:
    """Wymusza wzorzec tytulu '... in {color_manufacturer_text}' (color_manufacturer_text
    jest zrodlem prawdy dla koloru w tytule). Kolejnosc sprawdzania (pierwsze
    dopasowanie wygrywa):
    1. kolor juz poprawnie poprzedzony 'in'/'im' - bez zmian,
    1b. kolor (nawet w odmienionej formie przymiotnikowej) wystepuje gdzies w
        tytule, ale NIE jako dokladne slowo z (1) ani (2) - zwykle opisuje
        cos innego niz sam produkt (wzor/material, np. "mit schwarzem
        Schachbrettmuster"). Nie da sie bezpiecznie zgadnac, gdzie wstawic
        'in Farbe' bez ryzyka zepsucia gramatyki lub zdublowania koloru -
        zostawiamy tytul bez zmian (zostanie oflagowany do recznej
        weryfikacji przez brak wzorca 'in'/'im' w process_product()),
    2. kolor WYSTEPUJE w tytule (dokladne slowo), ale bez poprawnego
       przedrostka (przecinek, myslnik, nic) - wstaw ' in ' bezposrednio
       przed nim,
    3. tytul ma 'in X'/'im X' z INNYM kolorem X (kolor_value nie wystepuje
       nigdzie w tytule) - podmien X na color_value,
    4. kolor nigdzie nie wystepuje (nawet w formie odmienionej) i nie ma
       zadnego 'in X' - dopisz na koncu."""
    if not title or not color_value:
        return title, False

    # 1) kolor juz poprawny
    exact = re.compile(r"\b(in|im)\s+" + re.escape(color_value) + r"\b", re.IGNORECASE)
    if exact.search(title):
        return title, False

    # 2) kolor wystepuje w tytule, ale bez poprawnego 'in'/'im' przed nim
    present = re.compile(r"[,\-–—]?\s*" + re.escape(color_value) + r"\b", re.IGNORECASE)
    m = present.search(title)
    if m:
        color_start = m.end() - len(color_value)
        original_color = title[color_start:m.end()]
        new_title = title[:m.start()] + " in " + original_color + title[m.end():]
        new_title = re.sub(r"\s{2,}", " ", new_title).strip()
        return new_title, new_title != title

    # 3) tytul ma 'in X'/'im X' z innym kolorem - podmien X na color_value
    other = re.compile(r"\b(in|im)\s+([^,;()–—]+?)(?=\s*(?:[,;()–—]|$))", re.IGNORECASE)
    m = other.search(title)
    if m:
        new_title = title[:m.start()] + "in " + color_value + title[m.end():]
        new_title = re.sub(r"\s{2,}", " ", new_title).strip()
        return new_title, new_title != title

    # 1b) kolor w odmienionej formie przymiotnikowej wystepuje gdzies indziej
    # w tytule (patrz docstring) - nie zgadujemy, zostawiamy bez zmian.
    inflected = re.compile(re.escape(color_value) + _COLOR_INFLECTION_SUFFIX + r"\b", re.IGNORECASE)
    if inflected.search(title):
        return title, False

    # 4) kolor w ogole nie wystepuje w tytule - dopisz na koncu
    new_title = f"{title.rstrip()} in {color_value}"
    return new_title, True


def apply_title_fixes_excluding_model(text: str, model_name: str) -> tuple[str, bool]:
    """Stosuje naprawy formatowania/jezyka do tytulu, ALE nie dotyka fragmentu
    bedacego dokladnie nazwa modelu (jesli wystepuje w tytule jako podciag) -
    nazwa modelu/marki nie powinna byc tlumaczona."""
    if not text:
        return text, False
    model_name = (model_name or "").strip()
    if model_name and model_name in text:
        idx = text.index(model_name)
        before, after = text[:idx], text[idx + len(model_name):]
        before_fixed, ch1 = fix_double_spaces_and_grammar(before)
        before_fixed, ch1b = apply_translation_fixes(before_fixed)
        after_fixed, ch2 = fix_double_spaces_and_grammar(after)
        after_fixed, ch2b = apply_translation_fixes(after_fixed)
        new_text = before_fixed + model_name + after_fixed
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


# --- glowna funkcja per-produkt --------------------------------------------------

def process_product(row: OrderedDict, category_tree: CategoryTree) -> tuple[OrderedDict, list[dict]]:
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

    # --- tytul: naprawy formatowania + tlumaczenie EN->DE (poza nazwa modelu) ---
    title_field = _first_present(new_row, TITLE_CODE_CANDIDATES)
    if title_field and new_row.get(title_field):
        text = str(new_row[title_field])
        model_name = str(new_row.get("modelName_text", "") or "")
        text4, changed = apply_title_fixes_excluding_model(text, model_name)

        # Dopisanie/naprawa 'in' przed kolorem (kolor = aktualna wartosc
        # color_manufacturer_text, juz po ew. tlumaczeniu powyzej)
        color_value = str(new_row.get(color_field, "") or "") if color_field else ""
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
        dim_suffix = build_dimension_suffix(new_row)
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

    # --- genders / ages: tylko flagowanie (bez auto-tlumaczenia - poza zakresem) -
    gender_field = _first_present(new_row, GENDER_CODE_CANDIDATES)
    if gender_field and new_row.get(gender_field):
        _, _, note = normalize_gender(str(new_row[gender_field]))
        if note:
            issues.append(issue(gender_field, "GENDER_UNKNOWN_VALUE", note, "manual_review"))

    age_field = _first_present(new_row, AGE_CODE_CANDIDATES)
    if age_field and new_row.get(age_field):
        _, _, note = normalize_age(str(new_row[age_field]))
        if note:
            issues.append(issue(age_field, "AGE_UNKNOWN_VALUE", note, "manual_review"))

    # --- colors: sprawdzenie wzgledem zamknietego slownika kodow -----------------
    colors_field = _first_present(new_row, COLORS_FIELD_CANDIDATES)
    if colors_field and new_row.get(colors_field):
        _, _, note = normalize_colors_field(str(new_row[colors_field]))
        if note:
            issues.append(issue(colors_field, "COLOR_CODE_INVALID", note, "manual_review"))

    # --- modelName_text zanieczyszczony kolorem/rozmiarem (wykrycie, bez auto-fix) -
    model_field = "modelName_text" if "modelName_text" in new_row else None
    if model_field and new_row.get(model_field):
        model_val = str(new_row[model_field])
        size_val = str(new_row.get("sizes", "") or "")
        color_val = str(new_row.get(color_field, "") if color_field else "")
        if (size_val and size_val.lower() in model_val.lower()) or \
           (color_val and color_val.lower() in model_val.lower() and len(color_val) > 2):
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
        if field_code in new_row and new_row.get(field_code):
            raw = str(new_row[field_code])
            tokens = [t.strip() for t in raw.split("|") if t.strip()]
            unknown = [t for t in tokens if t not in allowed]
            if unknown:
                issues.append(issue(field_code, "ENUM_UNKNOWN_TOKEN",
                                     f"Nieznane tokeny {unknown} w polu {field_code} - brak w zaobserwowanym slowniku.",
                                     "manual_review"))

    return new_row, issues
