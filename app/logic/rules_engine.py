"""
Silnik regul poprawiajacych dane produktowe.

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
    if not value:
        return value, False, None
    v = value.strip()
    if v in D.GENDERS_VALID_DE:
        return v, False, None
    if v.lower() in D.GENDERS_EN_TO_DE:
        return D.GENDERS_EN_TO_DE[v.lower()], True, f"Przetlumaczono wartosc '{v}' na niemiecki token."
    return v, False, f"Nieznana wartosc '{v}' dla pola plci - brak w slowniku, wymaga sprawdzenia."


def normalize_age(value: str) -> tuple[str, bool, str | None]:
    if not value:
        return value, False, None
    v = value.strip()
    if v in D.AGES_VALID_DE:
        return v, False, None
    if v.lower() in D.AGES_EN_TO_DE:
        return D.AGES_EN_TO_DE[v.lower()], True, f"Przetlumaczono wartosc '{v}' na niemiecki token."
    return v, False, f"Nieznana wartosc '{v}' dla grupy wiekowej - brak w slowniku, wymaga sprawdzenia."


def fix_color_word(value: str) -> tuple[str, bool, str | None]:
    if not value:
        return value, False, None
    changed_any = False
    parts = re.split(r"([/\s])", value)  # zachowaj separatory "/" i spacje
    out = []
    for p in parts:
        key = p.strip().lower()
        if key in D.COLORS_EN_TO_DE:
            out.append(D.COLORS_EN_TO_DE[key])
            changed_any = True
        else:
            out.append(p)
    new_value = "".join(out)
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

    # --- tytul: naprawy formatowania -------------------------------------------
    title_field = _first_present(new_row, TITLE_CODE_CANDIDATES)
    if title_field and new_row.get(title_field):
        text = str(new_row[title_field])
        text2, ch1 = fix_double_spaces_and_grammar(text)
        text3, ch2 = fix_broken_umlauts(text2)
        if ch1 or ch2:
            new_row[title_field] = text3
            issues.append(issue(title_field, "TITLE_FORMAT_FIXED",
                                 f"Naprawiono formatowanie tytulu: '{text}' -> '{text3}'.",
                                 "auto_fixed"))
        if " in " not in text3 and " im " not in text3:
            issues.append(issue(title_field, "TITLE_PATTERN_MISSING",
                                 "Tytul nie zawiera wzorca 'Typ (+Model) in Farbe' - wymaga recznej weryfikacji.",
                                 "manual_review"))

    # --- kolor producenta: EN -> DE ---------------------------------------------
    color_field = _first_present(new_row, COLOR_MANUFACTURER_CANDIDATES)
    if color_field and new_row.get(color_field):
        new_val, changed, note = fix_color_word(str(new_row[color_field]))
        if changed:
            new_row[color_field] = new_val
            issues.append(issue(color_field, "COLOR_LANGUAGE_FIXED", note, "auto_fixed"))

    # --- material: format % + jezyk ----------------------------------------------
    mat_field = _first_present(new_row, MATERIAL_CODE_CANDIDATES)
    if mat_field and new_row.get(mat_field):
        new_val, changed, note = fix_material_composition(str(new_row[mat_field]))
        if changed:
            new_row[mat_field] = new_val
            issues.append(issue(mat_field, "MATERIAL_FORMAT_FIXED", note, "auto_fixed"))

    # --- genders / ages: tylko niemieckie tokeny ---------------------------------
    gender_field = _first_present(new_row, GENDER_CODE_CANDIDATES)
    if gender_field and new_row.get(gender_field):
        new_val, changed, note = normalize_gender(str(new_row[gender_field]))
        if changed:
            new_row[gender_field] = new_val
            issues.append(issue(gender_field, "GENDER_LANGUAGE_FIXED", note, "auto_fixed"))
        elif note:
            issues.append(issue(gender_field, "GENDER_UNKNOWN_VALUE", note, "manual_review"))

    age_field = _first_present(new_row, AGE_CODE_CANDIDATES)
    if age_field and new_row.get(age_field):
        new_val, changed, note = normalize_age(str(new_row[age_field]))
        if changed:
            new_row[age_field] = new_val
            issues.append(issue(age_field, "AGE_LANGUAGE_FIXED", note, "auto_fixed"))
        elif note:
            issues.append(issue(age_field, "AGE_UNKNOWN_VALUE", note, "manual_review"))

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
