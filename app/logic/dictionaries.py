"""
Slowniki normalizacyjne zbudowane WYLACZNIE na podstawie tego, co zaobserwowano
w 5 plikach good_data_1..5 oraz w Product_categories_guidelines.

Zgodnie z ustaleniami: nie zgadujemy nowych tokenow spoza obserwacji - jesli
wartosc nie jest w slowniku, pole jest oznaczane do recznej kontroli zamiast
byc "poprawianym" na sile.
"""

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
}

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
