"""
Struktura tytulu per grupa kategorii (zrodlo: Product_categories_guidelines.docx,
sekcje "Please follow the product title structure").

Grupa jest wykrywana ze sciezki kategorii DE (field_requirements.detect_group).
Profil decyduje, co process_product() automatycznie robi z tytulem:
  - color: True - tytul konczy sie "in <Kolor>" (dopisywane/poprawiane),
    False - bez koloru (Kosmetyki/Food/Literatura/Schmuck), "keep" - kolor
    nie jest dopisywany, ale jesli tytul juz go ma, zostaje i jest
    poprawiany (Spielwaren: struktura wytycznych bez koloru, ale np.
    przytulanki/zestawy tekstylne "Musselin Geschenk-Set ... in Rosa"),
  - dimensions: "onesize" - wymiary w tytule tylko dla produktow onesize
    (dopisywane z pol wymiarow, usuwane gdy produkt ma rozmiary), "none" -
    wymiary nigdy w tytule (usuwane), "keep" - nie ruszamy,
  - keep_gender: plec jest czescia tytulu (Brillen: "Damen-Sonnenbrille"),
  - keep_mit: klauzula "mit X" jest czescia tytulu (Schmuck: "Halskette mit
    Anhänger"),
  - restructure: False = tylko naprawa znakow/spacji (Literatura - tytul
    ksiazki nie moze byc tlumaczony ani przycinany),
  - suffixes: koncowki tytulu, ktore sa wymagane przez wytyczne (wiek, ilosc,
    EEK, rozmiar "Gr. 2") - odcinane przed czyszczeniem tytulu i doklejane z
    powrotem po kolorze, zeby czyszczenie (obcinanie fragmentu po modelu,
    usuwanie kodow rozmiaru typu "L") ich nie niszczylo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SEP = r"[\s,]*[-–—]?\s*"
_NUM = r"\d+(?:[.,]\d+)?"
_QTY_UNIT = r"(?:ml|cl|l|g|kg)"
_QTY_ITEM = rf"(?:\d+\s*x\s*)?{_NUM}\s*{_QTY_UNIT}\b"

SUFFIX_PATTERNS = {
    # "– (B)27 x (H)30 x (T)14 cm", "- Ø 25 cm", "– (H)15cm", "- (H)50 x ø 45cm", "- 9,8 x 14,5 cm"
    "dimensions": re.compile(
        rf"{_SEP}(?:\([A-Za-z]\)\s*)?(?:[øØ]\s*)?{_NUM}\s*"
        rf"(?:[x×]\s*(?:\([A-Za-z]\)\s*)?(?:[øØ]\s*)?{_NUM}\s*)*(?:mm|cm|m)\b\s*$"),
    # "– EEK A", "- EEK A++"
    "eek": re.compile(rf"{_SEP}EEK\s*[A-G]\+*\s*$"),
    # "- ab 3 Monaten", "– ab 4 Jahren", "- ab Geburt"
    "age": re.compile(
        rf"{_SEP}ab\s+(?:Geburt|\d+\s*(?:Monat(?:en|e)?|Jahr(?:en|e)?))\s*$", re.IGNORECASE),
    # "- Gr. 2", "- Gruppe 1/2/3"
    "size_group": re.compile(rf"{_SEP}(?:Gr\.\s*\d+|Gruppe\s+\d+(?:\s*/\s*\d+)*)\s*$"),
    # ", 100ml", ", je 125ml", ", 2 x 65g", ", 2x65g, 1x150ml", "- 2x 120 ml"
    "quantity": re.compile(
        rf"{_SEP}(?:je\s+)?{_QTY_ITEM}(?:\s*,\s*{_QTY_ITEM})*\s*$", re.IGNORECASE),
    # ", je 24 Teile"
    "pieces": re.compile(rf"{_SEP}(?:je\s+)?\d+\s+Teile\s*$", re.IGNORECASE),
}


@dataclass(frozen=True)
class TitleProfile:
    color: bool | str = True  # True | False | "keep"
    dimensions: str = "onesize"  # "onesize" | "none" | "keep"
    keep_gender: bool = False
    keep_mit: bool = False
    restructure: bool = True
    suffixes: tuple[str, ...] = ()


DEFAULT_PROFILE = TitleProfile()

TITLE_PROFILES = {
    "accessories": TitleProfile(),
    "bags_suitcases": TitleProfile(suffixes=("quantity",)),
    "books": TitleProfile(color=False, dimensions="keep", restructure=False),
    "clothing": TitleProfile(dimensions="none"),
    "baby_equipment": TitleProfile(suffixes=("age", "size_group", "quantity")),
    "toys": TitleProfile(color="keep", dimensions="none", suffixes=("age", "pieces", "quantity")),
    "cosmetics": TitleProfile(color=False, dimensions="keep", keep_gender=True,
                              suffixes=("quantity",)),  # "Antitranspirant für Männer, 100ml"
    "electronics": TitleProfile(dimensions="keep"),
    "food": TitleProfile(color=False, dimensions="keep", suffixes=("quantity",)),
    "furniture": TitleProfile(),
    "home_living": TitleProfile(suffixes=("quantity",)),
    "home_textiles": TitleProfile(),
    "jewellery": TitleProfile(color=False, dimensions="keep", keep_mit=True),
    "lamps": TitleProfile(suffixes=("eek",)),
    "pet_accessories": TitleProfile(),
    "shoes": TitleProfile(dimensions="none"),
    "sunglasses": TitleProfile(dimensions="none", keep_gender=True),
    "watches": TitleProfile(dimensions="none"),
}

_ONESIZE_VALUES = {"onesize", "one size", "one-size", "einheitsgröße", "einheitsgrösse"}


def profile_for(group: str | None) -> TitleProfile:
    return TITLE_PROFILES.get(group, DEFAULT_PROFILE)


def is_onesize(sizes_value) -> bool | None:
    """True/False wg pola `sizes`; None gdy pole puste/brak (nie wiadomo)."""
    val = str(sizes_value or "").strip().lower()
    if not val:
        return None
    return val in _ONESIZE_VALUES


def split_suffixes(title: str, profile: TitleProfile) -> tuple[str, list[tuple[str, str]]]:
    """Odcina od konca tytulu koncowki wymagane przez profil (oraz wymiary -
    zawsze, zeby mozna je bylo zastapic/usunac wg profile.dimensions).
    Zwraca (rdzen, [(rodzaj, tekst_koncowki)] w oryginalnej kolejnosci)."""
    kinds = ("dimensions",) + profile.suffixes
    found: list[tuple[str, str]] = []
    core = title
    while core:
        for kind in kinds:
            m = SUFFIX_PATTERNS[kind].search(core)
            if m and m.start() > 0:
                found.append((kind, core[m.start():]))
                core = core[:m.start()].rstrip()
                break
        else:
            break
    found.reverse()
    return core, found
