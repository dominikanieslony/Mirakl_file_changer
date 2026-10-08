"""
Slowniki normalizacyjne zbudowane WYLACZNIE na podstawie tego, co zaobserwowano
w 5 plikach good_data_1..5 oraz w Product_categories_guidelines.

Zgodnie z ustaleniami: nie zgadujemy nowych tokenow spoza obserwacji - jesli
wartosc nie jest w slowniku, pole jest oznaczane do recznej kontroli zamiast
byc "poprawianym" na sile.
"""
import csv
import os
from functools import lru_cache

# --- genders: POTWIERDZONE przez uzytkownika na podstawie definicji atrybutu w
# systemie (code/label sa tu identyczne, angielskie): female / male / unisex.
# Wszystko inne (np. niemieckie "männlich"/"weiblich") to bledna wartosc pola.
GENDERS_VALID_CODES = {"female", "male", "unisex"}

GENDERS_LABEL_TO_CODE = {
    # zaobserwowane niemieckie warianty (np. w ReferenceData z good_data_2.xlsx)
    "männlich": "male",
    "weiblich": "female",
    "unisex": "unisex",
}

# --- ages: POTWIERDZONE przez uzytkownika na podstawie definicji atrybutu w
# systemie (code/label). Prawidlowa wartosc w pliku importu to CODE (angielski,
# liczba pojedyncza): baby / child / adult. Wszystko inne (w tym niemieckie
# "Erwachsene"/"Kinder" i angielskie labelki w liczbie mnogiej "adults"/
# "children"/"babies") to LABEL do wyswietlania, nie poprawna wartosc pola.
AGES_VALID_CODES = {"baby", "child", "adult"}

AGES_LABEL_TO_CODE = {
    # angielskie labelki (liczba mnoga) z definicji atrybutu
    "adults": "adult",
    "children": "child",
    "babies": "baby",
    # zaobserwowane niemieckie warianty (np. w ood_data_2.xlsx) - traktowane
    # jako bledne/label zamiast code
    "erwachsene": "adult",
    "kinder": "child",
    "kleinkinder": "child",  # brak osobnego kodu "toddler" w systemie - najblizszy odpowiednik
    "babys": "baby",
}

# --- kolory: zaobserwowane angielskie/bledne formy -> poprawna niemiecka forma --
COLORS_EN_TO_DE = {
    "black": "Schwarz",
    "white": "Weiß",
    "grey": "Grau",
    "gray": "Grau",
    "blue": "Blau",
    "green": "Grün",
    "beige": "Beige",
    "rose": "Rosa",
    "anthracite": "Anthrazit",
    "brown": "Braun",
    "red": "Rot",
    "yellow": "Gelb",
    "purple": "Lila",
    "violet": "Violett",
    "silver": "Silber",
    "turquoise": "Türkis",
    "lilac": "Flieder",
}

# Slowa z COLORS_EN_TO_DE, ktore sa tez zwyklymi niemieckimi slowami - w prozie
# (Long Description) nie tlumaczymy ich ("Rose" = roza, "Rose: 9 x 9 mm" jako
# nazwa wariantu rosegold).
COLORS_EN_AMBIGUOUS_IN_PROSE = {"rose"}

# --- colors (Limango Color): POTWIERDZONE przez uzytkownika - pelna lista code
# (identyczna z label, angielska, lowercase, l. pojedyncza z myslnikami dla
# zlozen). To jest ZAMKNIETY slownik dopuszczalnych wartosci tego pola.
COLORS_VALID_CODES = {
    "anthracite", "beige", "black", "blue", "bordeaux", "bronze", "brown",
    "colorful", "cream", "cyan", "dark-blue", "gold", "gray", "green", "khaki",
    "light-blue", "light-brown", "light-green", "lilac", "multicolored",
    "nocolor", "olive", "orange", "others", "petrol", "pink", "purple", "red",
    "rose", "sand", "silver", "taupe", "transparent", "turquoise", "uncolored",
    "white", "yellow",
}

# najczesciej spotykane warianty pisowni/synonimy -> poprawny code
COLORS_LABEL_TO_CODE = {
    "grey": "gray",
    "darkblue": "dark-blue",
    "lightblue": "light-blue",
    "lightbrown": "light-brown",
    "lightgreen": "light-green",
    "multicolor": "multicolored",
    "multi-colored": "multicolored",
    "no color": "nocolor",
    "no-color": "nocolor",
}

# --- uzupelnianie pustego 'colors' z color_manufacturer_text (potwierdzone przez
# uzytkownika 2026-10-08: "bierzmy to z manufacturer color designation jak
# mozliwe"). Slowo koloru producenta (DE/EN, lowercase) -> code Limango Color.
MANUFACTURER_COLOR_TO_CODE = {
    "schwarz": "black", "black": "black",
    "weiß": "white", "weiss": "white", "white": "white",
    "grau": "gray", "grey": "gray", "gray": "gray",
    "anthrazit": "anthracite", "anthracite": "anthracite",
    "blau": "blue", "blue": "blue",
    "dunkelblau": "dark-blue", "navy": "dark-blue", "navyblau": "dark-blue",
    "marine": "dark-blue", "marineblau": "dark-blue", "nachtblau": "dark-blue",
    "darkblue": "dark-blue",
    "hellblau": "light-blue", "lightblue": "light-blue",
    "braun": "brown", "brown": "brown",
    "hellbraun": "light-brown", "lightbrown": "light-brown",
    "beige": "beige",
    "creme": "cream", "cream": "cream", "ecru": "cream", "offwhite": "cream",
    "grün": "green", "green": "green",
    "hellgrün": "light-green", "lightgreen": "light-green",
    "oliv": "olive", "olivgrün": "olive", "olive": "olive",
    "khaki": "khaki",
    "gelb": "yellow", "yellow": "yellow",
    "orange": "orange",
    "rot": "red", "red": "red",
    "bordeaux": "bordeaux", "weinrot": "bordeaux", "burgund": "bordeaux",
    "rosa": "rose", "rosé": "rose", "rose": "rose",
    "pink": "pink",
    "lila": "purple", "violett": "purple", "purple": "purple",
    "flieder": "lilac", "lilac": "lilac",
    "türkis": "turquoise", "turquoise": "turquoise",
    "petrol": "petrol",
    "gold": "gold", "silber": "silver", "silver": "silver", "bronze": "bronze",
    "sand": "sand", "taupe": "taupe",
    "transparent": "transparent",
    "mehrfarbig": "multicolored", "bunt": "multicolored", "farbig": "multicolored",
    "multicolor": "multicolored", "multicolored": "multicolored",
    "colorful": "multicolored",
}

# przedrostki odcienia, po ktorych odcieciu zostaje kolor bazowy
# ("Dunkelgrau" -> "grau"); sprawdzane dopiero, gdy cale slowo nie jest w mapie
# (hellblau/dunkelblau maja wlasne kody)
MANUFACTURER_COLOR_SHADE_PREFIXES = ("dunkel", "hell", "mittel", "tief", "pastell",
                                     "neon", "dark", "light")

# code Limango Color -> etykiety w szablonach DE (arkusz ReferenceData). Szablon
# de_DE z 2026-09 ma literowke 'tükis' - stad dwie wersje dla turquoise.
COLOR_CODE_DE_LABELS = {
    "anthracite": ["anthrazit"], "beige": ["beige"], "black": ["schwarz"],
    "blue": ["blau"], "bordeaux": ["bordeaux"], "bronze": ["bronze"],
    "brown": ["braun"], "cream": ["creme"], "dark-blue": ["dunkelblau"],
    "gold": ["gold"], "gray": ["grau"], "green": ["grün"], "khaki": ["khaki"],
    "light-blue": ["hellblau"], "light-brown": ["hellbraun"],
    "light-green": ["hellgrün"], "lilac": ["flieder"],
    "multicolored": ["bunt", "mehrfarbig"], "olive": ["oliv"],
    "orange": ["orange"], "petrol": ["petrol"], "pink": ["pink"],
    "purple": ["lila"], "red": ["rot"], "rose": ["rosa"], "sand": ["sand"],
    "silver": ["silber"], "taupe": ["taupe"], "turquoise": ["türkis", "tükis"],
    "white": ["weiß"], "yellow": ["gelb"], "transparent": ["transparent"],
}

# Niderlandzkie slowa typu produktu w tytulach DE (eksport Muchachomalo,
# 2026-10-08: "Chicamala Racerback - Dames Sportbh") -> niemiecka forma.
# Niderlandzkie slowa plci sa w rules_engine.TITLE_GENDER_WORDS.
TITLE_NL_TO_DE = {
    "sportbh": "Sport-BH",
}

# Naprawa uciecia niemieckich znakow specjalnych (obserwowane w tytulach pliku 1:
# "Grun" -> "Grün"). To NIE jest ogolny stemmer, tylko mapa slow faktycznie
# napotkanych jako zepsute w danych zrodlowych - rozszerzac w miare obserwacji.
BROKEN_UMLAUT_WORDS = {
    "Grun": "Grün",
    "grun": "grün",
    "Weiss": "Weiß",  # poprawna alternatywna pisownia, NIE traktujemy jako blad
}

# --- materialy: angielskie nazwy zaobserwowane w polu materialComposition_de ----
MATERIALS_EN_TO_DE = {
    "Polyamide": "Polyamid",
    "Elastane": "Elasthan",
    "Cotton": "Baumwolle",
    "Polyester": "Polyester",  # identyczne w obu jezykach
    "Leather": "Leder",
}

# --- textiles_careInstructions_text: zaobserwowany zbior dopuszczalnych tokenow -
CARE_INSTRUCTION_TOKENS = {
    "iron_no",
    "iron_yes",
    "not_suitable_for_tumble_dryer",
    "suitable_for_tumble_dryer",
    "30_degree_delicate_wash",
    "30_degree_wash",
    "40_degree_wash",
    "hand_wash_only",
}

# --- pozostale zaobserwowane pola enumeracyjne (clothing_*, underwearSwimwear_*) -
# Uwaga: to jest NIEPELNA lista - zawiera tylko tokeny faktycznie zaobserwowane
# w good_data_3.xml. Wartosci spoza tego zbioru NIE sa automatycznie odrzucane,
# tylko oznaczane jako "nieznany token, do weryfikacji".
# --- dopasowywanie kategorii: dodatkowe synonimy zaobserwowane w danych --------
# (np. "Armreif" w tytule produktu przypisanego bledy do "Halsketten", podczas gdy
# poprawna kategoria to "Armbaender" [kod 1597] - slowo "Armreif" nie wystepuje
# w etykiecie kategorii, wiec bez tego wpisu dopasowanie po slowach kluczowych
# by go nie znalazlo)
CATEGORY_EXTRA_KEYWORDS = {
    "1597": ["armreif", "armreifen", "armband", "armbänder"],  # Armbänder
    "1596": ["kette", "ketten", "halskette"],  # Halsketten
    "1705": ["charm", "charms"],  # Anhänger
    "10996": ["schmuckset", "adventskalender"],  # Schmuck-Sets
    "2304": ["schmuckbox", "reiseetui", "schmuckaufbewahrung", "schmuckkästchen"],  # Schmuckkästen
    "1648": ["fußkette", "fusskette", "fusskettchen", "fußkettchen"],  # Fußkettchen
    "1595": ["creolen", "creole", "ohrring", "ohrstecker", "ohrhänger"],  # Ohrringe
}

KNOWN_ENUM_TOKENS = {
    "clothing_underwire_text": {"clothing_underwire_no", "clothing_underwire_yes"},
    "clothing_shapeProperties_text": {
        "shapes_silhouette",
        "shapes_stomach_butt_thighs",
        "elastic_strong_shaping",
    },
    "textiles_careInstructions_text": CARE_INSTRUCTION_TOKENS,
}


# --- aliasy naglowkow kolumn: rozpoznany naglowek pliku -> kanoniczny kod ------
# techniczny (dokladnie ten, ktorego szuka logic/rules_engine.py i
# field_requirements.py). Niektore eksporty (np. Shopify/Mirakl w wersji
# "etykiety czytelne dla czlowieka") uzywaja angielskich nazw wyswietlanych
# zamiast kodow technicznych - bez tej normalizacji ZADNE pole w takim pliku
# nie zostaloby rozpoznane (process_product()/validate_product() dzialaja
# wylacznie po kodach technicznych), wiec caly plik przechodzilby przez
# aplikacje kompletnie nietkniety, bez zadnej poprawki ani walidacji.
# Zaobserwowane w realnym eksporcie CSV (2026-09-24):
# brandName bywa numerycznym ID (eksporty XML/CSV) zamiast nazwy - bez nazwy
# nie da sie usunac marki z tytulu. ID ustalone z plikow uzytkownika:
# 16445 - temp.txt (tytuly "JAKO-O Lunchbox-Set ..."), 22853 - eksport
# PURELEI (Product_4960_*.csv), 22814 - eksport Lucardi (ce-product-export),
# 1622 - eksport Muchachomalo (ce-product-export-202610051149), pod tym samym
# ID jest tez damska/dziewczeca linia "Chicamala" ("Chicamala Racerback -
# Sport-BH"). Kilka nazw jednej marki rozdzielamy "|" (patrz _brand_pattern).
# Pelna lista marek (kod -> nazwa) jest w data/brands.csv (eksport listy
# wartosci brandName z Mirakl, named_list_values); ponizej tylko uzupelnienia
# ponad te liste - dodatkowe linie produktowe pod tym samym ID.
BRAND_ID_TO_NAME = {
    "1622": "Muchachomalo|Chicamala",
}

_BRANDS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "brands.csv")


@lru_cache(maxsize=1)
def _brand_names_from_file() -> dict[str, str]:
    try:
        with open(_BRANDS_PATH, encoding="utf-8") as f:
            # "|" w samej nazwie ("F|23") to nie separator linii produktowych
            return {r["code"].strip(): r["name"].strip().replace("|", " ")
                    for r in csv.DictReader(f, delimiter=";")}
    except FileNotFoundError:
        return {}


def brand_name_for_id(code: str) -> str:
    """Nazwa marki dla numerycznego brandName (pusta, gdy kod nieznany)."""
    code = (code or "").strip()
    return BRAND_ID_TO_NAME.get(code) or _brand_names_from_file().get(code, "")


# Marki, ktorych nazwa jest zwyklym slowem tytulu (kolor, material, typ
# produktu) - zaobserwowane kolizje listy marek ze slowami w tytulach
# ("Gold", "Stahl", "Set", "Sneakers"...). Ich nie usuwamy z tytulu, bo
# zniszczylibysmy kolor/typ ("Kette in Gold", "3er-Set").
GENERIC_BRAND_WORDS = {
    "gold", "silber", "silver", "stahl", "schwarz", "weiß", "braun", "rot",
    "blau", "grün", "rosa", "pink", "coral", "ocean", "denim", "velvet",
    "set", "sneakers", "polo", "outdoor", "basic", "style", "mini", "charm",
    "pearl", "vintage", "sun", "ball", "big", "pro", "tiny",
}


FIELD_ALIASES = {
    "Category": "CATEGORY",
    "Shop SKU": "shop_sku",
    "Product Name": "ShortDescription_de",
    "Brand": "brandName",
    "Long Description": "LongDescription_de",
    "Manufacturer product type": "manufacturer_product_type_text_de",
    "Model name": "modelName_text",
    "Limango Color": "colors",
    "Manufacturer color designation": "color_manufacturer_text",
    "Gender": "genders",
    "Age Group": "ages",
    "EAN/GTIN": "Std_EAN",
    "Material Composition": "materialComposition_de",
    "DE size fashion": "sizes",
}


def canonical_field_name(name: str, existing_keys) -> str:
    """Zwraca kanoniczny kod techniczny dla rozpoznanego aliasu naglowka
    (patrz FIELD_ALIASES), o ile taki kanoniczny klucz nie jest juz obecny w
    existing_keys - zeby nie nadpisac/skolidowac z polem, ktore w pliku juz
    wystepuje pod wlasciwa nazwa (np. plik ma jednoczesnie "Produktname" i
    "ShortDescription_de" - to juz jest poprawnie obslugiwane gdzie indziej
    przez _first_present(), nie ruszamy tego). Bez dopasowania lub przy
    kolizji zwraca oryginalna nazwe bez zmian."""
    canonical = FIELD_ALIASES.get(name)
    if canonical and canonical not in existing_keys:
        return canonical
    return name


def normalize_headers(codes: list) -> list:
    """Mapuje liste naglowkow kolumn (w kolejnosci z pliku) na kanoniczne kody
    techniczne wszedzie tam, gdzie to bezpieczne (patrz canonical_field_name) -
    pozycja/kolejnosc kolumn jest zachowana, zmienia sie tylko nazwa. Uzywane
    przez parsers/tabular_parser.py (CSV/XLSX) i parsers/xml_parser.py, zeby
    reszta aplikacji zawsze widziala wylacznie kody techniczne, niezaleznie od
    tego, jakiej konwencji nazw kolumn uzywa plik zrodlowy."""
    original = {c for c in codes if isinstance(c, str)}
    claimed = set()
    result = []
    for c in codes:
        if isinstance(c, str):
            canonical = FIELD_ALIASES.get(c)
            if canonical and canonical not in original and canonical not in claimed:
                claimed.add(canonical)
                result.append(canonical)
                continue
        result.append(c)
    return result
