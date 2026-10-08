# Korekta danych produktowych

Aplikacja Streamlit do automatycznego poprawiania i przygotowywania danych
produktowych zgodnie z wytycznymi kategorii oraz drzewem kategorii z
`all_categories.json`.

## Struktura repozytorium

```
app.py                      - interfejs Streamlit (plik glowny)
requirements.txt            - zaleznosci Pythona
parsers/                    - wczytywanie i zapis plikow XML / CSV / XLSX
logic/categories.py         - obsluga drzewa kategorii (all_categories.json)
logic/dictionaries.py       - slowniki normalizacyjne
logic/rules_engine.py       - reguly automatycznej korekty
logic/field_requirements.py - wymagalnosc pol per grupa kategorii
logic/validator.py          - walidacja koncowa
logic/table_utils.py        - pomocnicze funkcje tabeli/podswietlania (bez zaleznosci od streamlit)
data/all_categories.json    - drzewo kategorii
data/brands.csv             - lista marek kod;nazwa (eksport listy wartosci brandName z Mirakl)
```

## Uruchomienie lokalne (opcjonalnie)

```
pip install -r requirements.txt
streamlit run app.py
```

## Wdrozenie: GitHub + Streamlit Community Cloud

Ponizej instrukcja, jak wgrac projekt na GitHub i podpiac go pod Streamlit Cloud,
tak zeby kazda zmiana w plikach na GitHubie (edytowana bezposrednio w przegladarce)
automatycznie odswiezala dzialajaca aplikacje - bez terminala na laptopie.

### 1. Utworzenie repozytorium na GitHub

1. Wejdz na https://github.com/new
2. Nadaj nazwe repozytorium (np. `korekta-danych-produktowych`)
3. Ustaw jako **Public** lub **Private** (Streamlit Community Cloud dziala z obiema
   opcjami, przy Private trzeba polaczyc konta GitHub i Streamlit)
4. NIE zaznaczaj "Add a README" (repozytorium ma zostac puste - pliki wgramy z tego
   folderu)
5. Kliknij "Create repository"

### 2. Wgranie plikow z tego folderu (najlatwiej przez przegladarke, bez terminala)

1. Na stronie swiezo utworzonego, pustego repozytorium kliknij link
   **"uploading an existing file"**
2. Przeciagnij i upusc **wszystkie pliki i foldery z tego folderu** (`app.py`,
   `requirements.txt`, `README.md`, `.gitignore`, cale foldery `logic/`, `parsers/`,
   `data/`) do okna przegladarki
3. Na dole strony wpisz opis commita (np. "Pierwsza wersja aplikacji") i kliknij
   **"Commit changes"**

(Alternatywnie, jesli wolisz uzyc gita raz na start, a pozniej juz edytowac tylko
przez GitHub: `git init && git add . && git commit -m "init" && git remote add origin <adres-repo> && git push -u origin main`)

### 3. Podpiecie pod Streamlit Community Cloud

1. Wejdz na https://share.streamlit.io i zaloguj sie kontem GitHub
2. Kliknij **"New app"**
3. Wybierz swoje repozytorium, branch `main`, a jako **"Main file path"** wpisz
   `app.py`
4. Kliknij **"Deploy"**

Po kilku minutach aplikacja bedzie dostepna pod publicznym adresem
`https://<nazwa-aplikacji>.streamlit.app`.

### 4. Wprowadzanie poprawek bez terminala

Od tej pory kazda zmiana pliku bezposrednio na GitHubie (np. przez wejscie w plik
w przegladarce, klikniecie ikony olowka "Edit this file", zmiane tresci i
"Commit changes") automatycznie uruchamia ponowne wdrozenie aplikacji na
Streamlit Cloud (widac to jako krotki "rebooting" w prawym dolnym rogu aplikacji,
zwykle trwajace do minuty).

Jesli zmiana dotyczy `requirements.txt` (nowa biblioteka), restart aplikacji
przez Streamlit Cloud trwa nieco dluzej, bo srodowisko jest instalowane od nowa.

## Zalozenia projektowe (przyjete decyzje)

1. Automatyczne tlumaczenie angielskich slow na niemieckie odbywa sie w: tytule
   (wszystkie skladowe OPROCZ dokladnego fragmentu = modelName_text, ktory nigdy
   nie jest modyfikowany), w Long Description, oraz w osobnych polach
   `materialComposition_de`/`Materialzusammensetzung` i
   `color_manufacturer_text`/`Herstellerfarbbezeichnung`.
1d. Tytul MUSI miec forme "Typ produktu + Model + in Kolor", gdzie Kolor to
   zawsze aktualna wartosc `color_manufacturer_text`. Aplikacja automatycznie:
   wstawia brakujace "in" (gdy kolor jest w tytule, ale bez przyimka - np. po
   przecinku/myslniku), PODMIENIA niezgodny kolor w tytule na wartosc z
   `color_manufacturer_text` (np. tytul "in Grau" a pole mowi "Dunkelgrau" ->
   "in Dunkelgrau"), oraz dopisuje kolor na koncu tytulu, jesli w ogole go tam
   nie ma.
1e. Struktura tytulu zalezy od grupy kategorii (wytyczne
   Product_categories_guidelines, profile w `logic/title_rules.py`; grupa
   wykrywana z 2. poziomu sciezki kategorii w `field_requirements.detect_group`):
   - bez "in Kolor": Beauty und Parfum, Food, Literatura, Schmuck,
   - Spielwaren: kolor nie jest dopisywany, ale jesli tytul juz go ma, zostaje
     (przytulanki, zestawy tekstylne - np. "Musselin Geschenk-Set ... in Rosa"),
   - wymiary w tytule tylko dla `sizes = onesize` (dopisywane z pol
     width/height/depth_numeric, usuwane przy produktach z rozmiarami):
     Taschen/Reisegepäck, Möbel, Home & Living, Heimtextilien, Lampen,
     Tierbedarf, Babyartikel, akcesoria (Schals, Gürtel...); nigdy w tytule:
     Bekleidung, Schuhe, Brillen, Uhren, Spielwaren,
   - zachowywane koncowki: wiek ("- ab 3 Jahren"), "Gr. 2"/"Gruppe 1/2/3",
     ilosc (", 100ml", ", 2 x 65g"), "EEK A", ", je 24 Teile",
   - Brillen: plec zostaje ("Damen-Sonnenbrille"); Schmuck: "mit X" zostaje
     ("Halskette mit Anhänger"); "mit ...anteil" (Schals) zawsze zostaje,
   - Literatura: tytul ksiazki tylko z naprawa znakow/spacji.
   - tytul bedacy samym modelem ("Hawk") dostaje typ produktu z
     `manufacturer_product_type_text_de` ("Hawk Stunt Scooter" -> "Stunt
     Scooter Hawk in ..."), tylko gdy pole ma forme "model + typ" (rzeczowniki).
   Elementy, ktorych nie da sie bezpiecznie wywnioskowac z danych (Leder-/
   Gold- na poczatku, EEK, pojemnosc, kroj jeansow), nie sa ani dopisywane,
   ani flagowane.
1f. `color_manufacturer_text` jest zawsze tlumaczony na niemiecki
   (`fix_manufacturer_color`): nazwy kolorow EN/NL/FR/IT/ES/PL/DA/SV, frazy
   ("Off White"), odcienie przed i po kolorze ("Donker Grijs", "Donkerbruin",
   "Bleu foncé", "GRIS F" -> "Dunkelgrau", "Dunkelbraun", "Dunkelblau",
   "Dunkelgrau"). Nieznane slowa (motywy: "Flamingo", "Stars") zostaja.
   modelName_text w obcym jezyku / ogolny typ ("Compressiesokken", "Klompen
   dames" - wspolny dla >=3 grup wariantow albo ze slowem plci) nie jest
   doklejany do tytulu, jesli go w tytule nie ma.
1a. `ages` (Age Group) - potwierdzone przez uzytkownika na podstawie definicji
   atrybutu w systemie: poprawna wartosc pola to CODE (angielski, l. pojedyncza:
   `baby`/`child`/`adult`), NIE label wyswietlany w UI (`babies`/`children`/`adults`
   ani niemieckie odpowiedniki typu "Erwachsene"). Wartosc jest tylko flagowana do
   recznej weryfikacji, nie zmieniana automatycznie.
1b. `genders` (Gender) - potwierdzone przez uzytkownika: code i label sa tu
   identyczne (angielskie): `female`/`male`/`unisex`. Niemieckie odpowiedniki
   ("männlich"/"weiblich") sa flagowane jako bledna wartosc pola.
1c. `colors` (Limango Color) - potwierdzone przez uzytkownika: pelna, zamknieta
   lista 36 kodow (identyczne z label, angielskie, lowercase, np. `dark-blue`,
   `nocolor`, `multicolored`). Wartosci spoza tej listy sa flagowane.
2. Marka jest usuwana z tytulu. Gdy `brandName` to numeryczny kod, nazwa jest
   brana z `data/brands.csv` (aktualizacja: podmienic plik nowym eksportem
   kod;nazwa). Kod spoza listy jest raportowany do recznej weryfikacji. Marki
   o nazwie bedacej zwyklym slowem tytulu (Gold, Stahl, Set... - patrz
   `GENERIC_BRAND_WORDS`) nie sa usuwane, zeby nie zniszczyc koloru/typu.
3. Slowniki tokenow enumeracyjnych obejmuja wylacznie wartosci zaobserwowane w danych
   przykladowych - nieznane tokeny sa oznaczane do recznej weryfikacji.
4. Macierz wymagalnosci pol per kategoria jest zaszyta na sztywno w `field_requirements.py`.
5. Przed pobraniem pliku aplikacja ostrzega o liczbie pozycji do recznej weryfikacji,
   podswietla dokladne pola wymagajace uwagi i pozwala je edytowac bezposrednio w tabeli.
