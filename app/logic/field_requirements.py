"""
Macierz wymagalnosci pol per grupa kategorii - zaszyta na sztywno (decyzja uzytkownika).

Zrodla:
- Product_categories_guidelines: opisowe wymogi per grupa (tytul, opis: zastosowanie/
  produkcja/pielegnacja/zawartosc zestawu).
- good_data_2.xlsx / good_data_5.xlsx arkusz "Columns": konkretne kody pol REQUIRED
  dla grup "Bags & suitcases" i "Jewellery".

Dla grup, dla ktorych nie mielismy przykladu arkusza "Columns" (Clothing, Shoes,
Cosmetics, Electronics, Food, Furniture, Home & living, Home textiles, Books,
Children's..., Pet accessories, Sunglasses, Watches, Lamps & luminaires), stosujemy
uniwersalny rdzen + sprawdzenie tresciowe opisu (np. czy wspomina o pielegnacji),
zgodnie z tym, co faktycznie opisuja wytyczne dla tych grup.
"""
from __future__ import annotations

CORE_REQUIRED_FIELDS = [
    "CATEGORY",
    "ShortDescription_de",
    "LongDescription_de",
    "colors",
    "Std_EAN",
    "shop_sku",
]

# grupa -> (slowa kluczowe wystepujace w sciezce kategorii DE, uzywane do wykrycia grupy)
GROUP_PATH_KEYWORDS = {
    "bags_suitcases": ["reisegepäck", "taschen", "koffer"],
    "jewellery": ["schmuck"],
    "shoes": ["schuhe"],
    "clothing": ["bekleidung"],
    "home_textiles": ["heimtextilien"],
    "home_living": ["home und living", "haushaltswaren", "deko"],
    "furniture": ["möbel"],
    "cosmetics": ["beauty und parfum"],
    "electronics": ["technik"],
    "food": ["food"],
    "books": ["literatur"],
    "sunglasses": ["brillen"],
    "watches": ["uhren"],
    "lamps": ["lampen"],
    "pet_accessories": ["tierbedarf"],
    "baby_equipment": ["babyartikel", "kinderwagen", "kindermöbel"],
}

# dodatkowe pola REQUIRED per grupa (poza rdzeniem) - potwierdzone z arkusza Columns
GROUP_EXTRA_REQUIRED_FIELDS = {
    "bags_suitcases": ["sizes"],  # w praktyce "onesize" + objetosc w tytule/opisie
    "jewellery": ["sizes"],  # potwierdzone jako REQUIRED w Columns, mimo ze czesto puste w danych
}

# tresc opisu (LongDescription_de) powinna wspominac o okreslonych aspektach -
# sprawdzane jako "manual_review", jesli brak (nie da sie tego bezpiecznie dopisac
# automatycznie bez zmyslania faktow o produkcie)
GROUP_DESCRIPTION_SHOULD_MENTION = {
    "clothing": ["waschbar", "wäsche", "pflege", "grad"],
    "shoes": ["material", "pflege"],
    "bags_suitcases": ["material", "maße"],
    "home_textiles": ["material", "pflege"],
}


def detect_group(path_de: str | None) -> str | None:
    if not path_de:
        return None
    path_lower = path_de.lower()
    for group, keywords in GROUP_PATH_KEYWORDS.items():
        if any(kw in path_lower for kw in keywords):
            return group
    return None


def required_fields_for(group: str | None) -> list[str]:
    fields = list(CORE_REQUIRED_FIELDS)
    if group and group in GROUP_EXTRA_REQUIRED_FIELDS:
        fields.extend(GROUP_EXTRA_REQUIRED_FIELDS[group])
    return fields
