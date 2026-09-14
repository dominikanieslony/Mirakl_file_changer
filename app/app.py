"""
Aplikacja Streamlit do automatycznego poprawiania danych produktowych zgodnie
z Product_categories_guidelines oraz drzewem kategorii all_categories.json.

Uruchomienie lokalne:
    pip install -r requirements.txt
    streamlit run app.py
"""
from __future__ import annotations

import os
import tempfile
from collections import OrderedDict

import pandas as pd
import streamlit as st

from logic.categories import CategoryTree
from logic.rules_engine import process_product
from logic.validator import validate_product
from logic.table_utils import (
    FLAG_COLUMN,
    add_flag_column,
    build_issue_maps,
    build_styler,
    dataframe_to_products,
    products_to_dataframe,
)
from parsers import loader

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CATEGORIES_PATH = os.path.join(APP_DIR, "data", "all_categories.json")

st.set_page_config(page_title="Korekta danych produktowych", layout="wide")

# Pandas Styler generuje CSS per-komorke w Pythonie - dla bardzo duzych tabel
# (setki tysiecy+ komorek) jest to na tyle wolne/pamieciochlonne, ze na Streamlit
# Community Cloud potrafi zawiesic aplikacje (OOM), a nie tylko rzucic wyjatek.
# Dlatego zamiast bezmyslnie podnosic limit pandas, sami ograniczamy kiedy w ogole
# probujemy stylowac (patrz MAX_STYLED_CELLS w main()); limit pandas podnosimy
# tylko jako siatke bezpieczenstwa dla przypadkow tuz nad naszym progiem.
pd.set_option("styler.render.max_elements", 1_000_000)

# Powyzej tylu komorek w podgladzie pomijamy kolorowe podswietlenie (Styler) i
# pokazujemy zwykla, szybka tabele - uzytkownik moze zawezic widok checkboxem
# ponizej albo skorzystac z zakladki 'Raport problemow'.
MAX_STYLED_CELLS = 300_000

# Domyslny sufit liczby wierszy przetwarzanych na raz - powyzej tego progu
# proponujemy uzytkownikowi ograniczenie (przetwarzanie duzych plikow partiami),
# zeby zmniejszyc obciazenie (czas przetwarzania + pamiec na renderowanie).
MAX_ROWS_DEFAULT = 2000

# Kolumny faktycznie analizowane/poprawiane przez rules_engine.py i validator.py
# (patrz *_CANDIDATES w rules_engine.py, KNOWN_ENUM_TOKENS w dictionaries.py oraz
# CORE_REQUIRED_FIELDS/GROUP_EXTRA_REQUIRED_FIELDS w field_requirements.py).
# UWAGA: wczytujemy WYLACZNIE te kolumny - wszystkie pozostale pola z pliku
# zrodlowego (cena, zdjecia, stan magazynowy, inne EAN-y itd.) sa odrzucane i
# NIE trafiaja do pliku wynikowego (decyzja uzytkownika - swiadoma utrata danych
# w zamian za mniejsza liczbe komorek do przetworzenia/wyrenderowania).
RELEVANT_COLUMNS = {
    "CATEGORY", "Kategorie",
    "ShortDescription_de", "Produktname",
    "LongDescription_de", "Langbeschreibung",
    "color_manufacturer_text", "Herstellerfarbbezeichnung",
    "materialComposition_de", "Materialzusammensetzung",
    "genders", "Geschlecht",
    "ages", "Altersgruppe",
    "colors", "Limango Farbe",
    "modelName_text",
    "sizes",
    "brandName", "Marke",
    "width_numeric", "height_numeric", "depth_numeric",
    "width_numeric_unit", "height_numeric_unit",
    "clothing_underwire_text", "clothing_shapeProperties_text", "textiles_careInstructions_text",
    "Std_EAN", "shop_sku", "Shop SKU",
}


def filter_relevant_columns(products: list[OrderedDict]) -> list[OrderedDict]:
    return [
        OrderedDict((k, v) for k, v in row.items() if k in RELEVANT_COLUMNS)
        for row in products
    ]


@st.cache_resource
def get_category_tree() -> CategoryTree:
    return CategoryTree.load(CATEGORIES_PATH)


def run_pipeline(products, category_tree):
    corrected = []
    all_issues = []
    for idx, row in enumerate(products):
        fixed_row, issues = process_product(row, category_tree)
        val_issues = validate_product(fixed_row, category_tree)
        issues = issues + val_issues
        sku = fixed_row.get("shop_sku") or fixed_row.get("Shop SKU") or f"wiersz_{idx+1}"
        for iss in issues:
            all_issues.append({
                "line-number": idx + 1,
                "provider-unique-identifier": sku,
                **iss,
            })
        corrected.append(fixed_row)
    return corrected, all_issues


def main():
    st.title("Automatyczna korekta danych produktowych")
    st.caption(
        "Wgraj plik z danymi produktow (XML / CSV / XLSX). Aplikacja poprawi dane "
        "zgodnie z wytycznymi kategorii i sprawdzi przypisanie do kategorii na "
        "podstawie all_categories.json."
    )

    uploaded = st.file_uploader("Plik z danymi produktow", type=["xml", "csv", "xlsx", "xls"])
    if uploaded is None:
        st.info("Wgraj plik, aby rozpoczac.")
        return

    suffix = os.path.splitext(uploaded.name)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded.getbuffer())
        tmp_path = tmp.name

    try:
        products, file_format, meta = loader.load(tmp_path)
    except Exception as e:
        st.error(f"Nie udalo sie wczytac pliku: {e}")
        return

    total_cols_original = len(products[0]) if products else 0
    products = filter_relevant_columns(products)
    kept_cols = len(products[0]) if products else 0

    st.success(f"Wczytano {len(products)} pozycji produktowych (format: {file_format}).")
    if total_cols_original:
        st.caption(
            f"Wczytano tylko kolumny faktycznie analizowane przez aplikacje: "
            f"{kept_cols} z {total_cols_original} kolumn oryginalnego pliku. "
            f"Pozostale kolumny (np. cena, zdjecia, stan magazynowy) NIE zostana "
            f"uwzglednione w pliku wynikowym do pobrania."
        )

    total_rows = len(products)
    if total_rows > MAX_ROWS_DEFAULT:
        st.warning(
            f"Plik zawiera {total_rows} pozycji - przetwarzanie i podglad tak duzej "
            f"liczby wierszy naraz moze byc wolne lub przeciazyc aplikacje. Ponizej "
            f"mozesz ograniczyc liczbe przetwarzanych wierszy (np. przetwarzaj plik "
            f"partiami)."
        )
    max_rows = st.number_input(
        "Maksymalna liczba wierszy do przetworzenia (0 = wszystkie)",
        min_value=0,
        value=MAX_ROWS_DEFAULT if total_rows > MAX_ROWS_DEFAULT else 0,
        step=100,
    )
    if max_rows and max_rows < total_rows:
        products = products[:max_rows]
        st.info(f"Ograniczono do pierwszych {max_rows} z {total_rows} wierszy.")

    if "processed" not in st.session_state:
        st.session_state.processed = False

    if st.button("Przetworz i popraw dane", type="primary"):
        category_tree = get_category_tree()
        with st.spinner("Analizuje i poprawiam dane..."):
            corrected, all_issues = run_pipeline(products, category_tree)
        st.session_state.corrected_products = corrected
        st.session_state.issues = all_issues
        st.session_state.file_format = file_format
        st.session_state.meta = meta
        st.session_state.processed = True

    if not st.session_state.processed:
        return

    corrected = st.session_state.corrected_products
    all_issues = st.session_state.issues

    auto_fixed = [i for i in all_issues if i["severity"] == "auto_fixed"]
    manual_review = [i for i in all_issues if i["severity"] == "manual_review"]

    col1, col2, col3 = st.columns(3)
    col1.metric("Produkty", len(corrected))
    col2.metric("Automatyczne poprawki", len(auto_fixed))
    col3.metric("Do recznej weryfikacji", len(manual_review))

    if manual_review:
        st.warning(
            f"Uwaga: {len(manual_review)} pozycji moze zawierac blad, ktorego nie "
            f"mozna bylo jednoznacznie rozwiazac automatycznie. Sprawdz zakladke "
            f"'Podglad z podswietleniem' (zolte pola = do sprawdzenia) i w razie "
            f"potrzeby popraw dane recznie w tabeli w zakladce 'Poprawione dane' "
            f"przed pobraniem pliku."
        )

    tabs = st.tabs(["Podglad z podswietleniem", "Poprawione dane (edytowalne)", "Raport problemow"])

    issues_by_row, summary_by_row = build_issue_maps(all_issues)

    with tabs[0]:
        st.caption(
            "Zolte komorki to konkretne pola wymagajace recznej weryfikacji. "
            "Kolumna 'Pola do sprawdzenia' podsumowuje je per wiersz. To widok "
            "tylko do podgladu - edycji dokonaj w zakladce 'Poprawione dane'."
        )
        preview_df = products_to_dataframe(corrected)
        preview_df = add_flag_column(preview_df, summary_by_row)
        if manual_review:
            only_flagged = st.checkbox("Pokaz tylko wiersze wymagajace weryfikacji", value=True)
            view_df = preview_df[preview_df[FLAG_COLUMN] != ""] if only_flagged else preview_df
        else:
            view_df = preview_df
        if view_df.size > MAX_STYLED_CELLS:
            st.info(
                f"Widok ma {view_df.size} komorek - kolorowe podswietlenie pol "
                f"zostalo pominiete ze wzgledu na wydajnosc. Zawez widok checkboxem "
                f"powyzej albo sprawdz zakladke 'Raport problemow', aby zobaczyc "
                f"konkretne bledy."
            )
            st.dataframe(view_df, width="stretch")
        else:
            styled = build_styler(view_df, issues_by_row)
            st.dataframe(styled, width="stretch")

    with tabs[1]:
        df = products_to_dataframe(corrected)
        df = add_flag_column(df, summary_by_row)
        edited_df = st.data_editor(df, width="stretch", num_rows="fixed", key="editor")

    with tabs[2]:
        if all_issues:
            report_df = pd.DataFrame(all_issues)[
                ["line-number", "provider-unique-identifier", "attribute", "error_code", "severity", "message"]
            ]
            st.dataframe(report_df, width="stretch")
        else:
            st.info("Nie znaleziono zadnych problemow.")

    st.divider()
    out_name = f"poprawione_{uploaded.name}"
    if st.button("Przygotuj plik do pobrania"):
        final_products = dataframe_to_products(edited_df)
        out_path = os.path.join(tempfile.gettempdir(), out_name)
        loader.save(final_products, st.session_state.file_format, st.session_state.meta, out_path)
        with open(out_path, "rb") as f:
            st.download_button(
                "Pobierz poprawiony plik",
                data=f.read(),
                file_name=out_name,
            )


if __name__ == "__main__":
    main()
