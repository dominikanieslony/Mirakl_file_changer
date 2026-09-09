"""
Slowniki normalizacyjne zbudowane WYLACZNIE na podstawie tego, co zaobserwowano
w 5 plikach good_data_1..5 oraz w Product_categories_guidelines.

Zgodnie z ustaleniami: nie zgadujemy nowych tokenow spoza obserwacji - jesli
wartosc nie jest w slowniku, pole jest oznaczane do recznej kontroli zamiast
byc "poprawianym" na sile.
"""

# --- genders: tylko niemieckie tokeny sa docelowo poprawne ---------------------
GENDERS_EN_TO_DE = {
    "male": "männlich",
    "female": "weiblich",
    "unisex": "unisex",
    "boy": "Jungen",
    "girl": "Mädchen",
    "kids": "Kinder",
}
GENDERS_VALID_DE = {"männlich", "weiblich", "unisex", "Jungen", "Mädchen", "Kinder"}

# --- ages: tylko niemieckie tokeny sa docelowo poprawne -------------------------
AGES_EN_TO_DE = {
    "adult": "Erwachsene",
    "child": "Kinder",
    "children": "Kinder",
    "baby": "Babys",
    "toddler": "Kleinkinder",
}
AGES_VALID_DE = {"Erwachsene", "Kinder", "Babys", "Kleinkinder"}

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
