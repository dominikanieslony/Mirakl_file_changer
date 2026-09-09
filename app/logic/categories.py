"""
Obsluga drzewa kategorii na bazie all_categories.json.

Kluczowe funkcje:
- wczytanie kategorii i zbudowanie relacji parent/child
- wykluczenie kategorii testowych/smieciowych (9_9_*, 201*, 901*, TST_*, testSchuhe...)
- zbudowanie pelnej sciezki etykiet DE dla kazdego lisci ("Accessoires/Schmuck/Halsketten")
- dwukierunkowe mapowanie: category_code <-> sciezka tekstowa
- dopasowanie najbardziej prawdopodobnej kategorii na podstawie tekstu produktu (tytul/opis/typ)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from . import dictionaries as D


# Prefiksy/kody kategorii testowych, ktore nigdy nie powinny byc sugerowane ani uzywane
_TEST_PREFIXES = ("9_9", "201", "901", "TST_")
_TEST_EXACT = {"testSchuhe"}


def _is_test_category(code: str) -> bool:
    if code in _TEST_EXACT:
        return True
    return any(code.startswith(p) for p in _TEST_PREFIXES)


@dataclass
class Category:
    code: str
    label_default: str
    labels: dict = field(default_factory=dict)  # locale -> label
    parent_code: Optional[str] = None
    is_test: bool = False

    def label_de(self) -> Optional[str]:
        return self.labels.get("de_DE")


class CategoryTree:
    def __init__(self, categories: dict[str, Category]):
        self.categories = categories
        self._path_cache: dict[str, str] = {}
        self._path_to_code: dict[str, str] = {}
        self._build_paths()

    @classmethod
    def load(cls, json_path: str) -> "CategoryTree":
        with open(json_path, encoding="utf-8") as f:
            raw = json.load(f)
        cats: dict[str, Category] = {}
        for c in raw["categories"]:
            code = c["category_code"]
            labels = {t["locale"]: t["value"] for t in c.get("category_label_translations", [])}
            cats[code] = Category(
                code=code,
                label_default=c.get("category_label", ""),
                labels=labels,
                parent_code=c.get("parent_code"),
                is_test=_is_test_category(code),
            )
        return cls(cats)

    def _build_paths(self):
        for code, cat in self.categories.items():
            if cat.is_test:
                continue
            path = self._path_for(code)
            if path:
                self._path_cache[code] = path
                # normalizacja klucza do wyszukiwania: lowercase, bez wielokrotnych spacji
                self._path_to_code[path.lower()] = code

    def _path_for(self, code: str) -> Optional[str]:
        chain = []
        seen = set()
        cur = code
        while cur and cur not in seen:
            seen.add(cur)
            cat = self.categories.get(cur)
            if not cat or cat.is_test:
                return None
            label = cat.label_de() or cat.label_default
            if not label:
                return None
            chain.append(label)
            cur = cat.parent_code
        if not chain:
            return None
        return "/".join(reversed(chain))

    def is_leaf(self, code: str) -> bool:
        return not any(c.parent_code == code for c in self.categories.values())

    def path_de(self, code: str) -> Optional[str]:
        return self._path_cache.get(code)

    def code_from_path(self, path: str) -> Optional[str]:
        """Zwraca category_code dla pelnej sciezki tekstowej DE (np. z pliku XLSX)."""
        if not path:
            return None
        key = path.strip().lower()
        # tolerancja na rozne separatory ("/", "--", ">")
        for sep in ("--", ">", "|"):
            key = key.replace(sep, "/")
        key = re.sub(r"\s*/\s*", "/", key)
        return self._path_to_code.get(key)

    def resolve(self, category_value: str) -> Optional[str]:
        """Zwraca kanoniczny category_code niezaleznie od tego, czy wejscie to
        czysty kod, czy pelna sciezka tekstowa."""
        if not category_value:
            return None
        category_value = str(category_value).strip()
        if category_value in self.categories and not self.categories[category_value].is_test:
            return category_value
        return self.code_from_path(category_value)

    def label_for(self, code: str) -> Optional[str]:
        cat = self.categories.get(code)
        if not cat:
            return None
        return cat.label_de() or cat.label_default

    def all_leaf_codes(self):
        return [c for c in self.categories if not self.categories[c].is_test and self.is_leaf(c)]

    def siblings(self, code: str) -> list[str]:
        cat = self.categories.get(code)
        if not cat or not cat.parent_code:
            return []
        return [
            c for c, other in self.categories.items()
            if other.parent_code == cat.parent_code and c != code and not other.is_test
        ]

    def find_set_sibling(self, code: str) -> Optional[str]:
        """Jesli obecna kategoria jest 'pojedyncza' (np. 'Koffer'), a wsrod kategorii
        siostrzanych istnieje jej odpowiednik 'zestawowy' (np. 'Koffer-Sets'), zwraca
        jej kod. Dedykowana reguła dla wzorca 'Xtlg. Set' z wytycznych."""
        label = (self.label_for(code) or "").strip().lower()
        if not label or "set" in label:
            return None
        base = re.sub(r"[^a-zäöüß0-9]", "", label)
        for sib_code in self.siblings(code):
            sib_label = (self.label_for(sib_code) or "").lower()
            if "set" not in sib_label:
                continue
            sib_base = re.sub(r"[^a-zäöüß0-9]", "", sib_label.replace("set", ""))
            if base and base == sib_base.rstrip("s"):
                return sib_code
            if base and sib_base.startswith(base):
                return sib_code
        return None

    # --- dopasowywanie kategorii na podstawie tresci produktu -------------------

    @staticmethod
    def normalize_text(text: str) -> str:
        return _normalize(text)

    def _label_token_set(self, code: str) -> tuple[set, set]:
        label = self.label_for(code) or ""
        label_tokens = {_stem(t) for t in _normalize(label).split() if len(t) >= 4}
        extra_tokens = {_stem(t) for t in D.CATEGORY_EXTRA_KEYWORDS.get(code, []) if len(t) >= 4}
        return label_tokens, extra_tokens

    def current_category_supported_by_text(self, code: str, text: str) -> bool:
        """Sprawdza czy PRZYNAJMNIEJ JEDNO nietrywialne slowo z etykiety obecnie
        przypisanej kategorii pojawia sie w tekscie (po prostym stemowaniu, zeby
        odpornosc na liczbe mnoga/pojedyncza typu Sneaker/Sneakers)."""
        label_tokens, extra_tokens = self._label_token_set(code)
        text_tokens = {_stem(t) for t in _normalize(text).split()}
        return bool((label_tokens | extra_tokens) & text_tokens)

    def suggest_category(self, text: str, top_n: int = 3) -> list[tuple[str, str, int]]:
        """
        Prosta, przejrzysta heurystyka dopasowania oparta na slowach kluczowych
        (z prostym stemowaniem, zeby ograniczyc szum typu liczba pojedyncza/mnoga).
        Zwraca liste (category_code, path_de, score) posortowana malejaco.

        To celowo prosta metoda - nie zgaduje kategorii z niska pewnoscia; wyniki
        o niskim score nie powinny byc uzywane do automatycznej zmiany, tylko
        oflagowane do recznej kontroli.
        """
        text_tokens = {_stem(t) for t in _normalize(text).split()}
        scored = []
        for code in self.all_leaf_codes():
            path = self.path_de(code)
            if not path:
                continue
            label_tokens, extra_tokens = self._label_token_set(code)
            if not label_tokens and not extra_tokens:
                continue
            overlap = len(label_tokens & text_tokens)
            overlap += 3 * len(extra_tokens & text_tokens)
            if overlap > 0:
                scored.append((code, path, overlap))
        scored.sort(key=lambda x: x[2], reverse=True)
        return scored[:top_n]


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-zäöüß0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _stem(word: str) -> str:
    """Bardzo prosty 'stemmer' ograniczajacy szum z liczby pojedynczej/mnogiej
    (np. sneaker/sneakers) - NIE jest to pelny stemmer jezykowy, tylko heurystyka
    obcinajaca typowe koncowki mnoga."""
    for suffix in ("nen", "en", "er", "es", "n", "e", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word
