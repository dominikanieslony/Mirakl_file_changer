# Korekta danych produktowych

## Uruchomienie
```
pip install -r requirements.txt
streamlit run app.py
```

## Struktura
- `app.py` - interfejs Streamlit
- `parsers/` - wczytywanie i zapis plikow XML / CSV / XLSX
- `logic/categories.py` - obsluga drzewa kategorii (all_categories.json)
- `logic/dictionaries.py` - slowniki normalizacyjne (obserwowane w danych)
- `logic/rules_engine.py` - reguly automatycznej korekty
- `logic/field_requirements.py` - wymagalnosc pol per grupa kategorii
- `logic/validator.py` - walidacja koncowa
- `data/all_categories.json` - drzewo kategorii dostarczone przez uzytkownika

## Zalozenia (na podstawie decyzji uzytkownika)
1. `genders`/`ages` docelowo maja byc po niemiecku - angielskie tokeny sa tlumaczone.
2. Niespojnosci formatu `brandName` (numeryczne ID vs tekst) sa tylko raportowane.
3. Slowniki tokenow enumeracyjnych obejmuja wylacznie wartosci zaobserwowane w danych
   przykladowych - nieznane tokeny sa oznaczane do recznej weryfikacji.
4. Macierz wymagalnosci pol per kategoria jest zaszyta na sztywno w `field_requirements.py`.
5. Przed pobraniem pliku aplikacja ostrzega o liczbie pozycji do recznej weryfikacji
   i pozwala je edytowac bezposrednio w tabeli.
