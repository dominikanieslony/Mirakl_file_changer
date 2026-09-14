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

# Domyslny limit pandas Styler (262144 komorek = wiersze x kolumny) jest za niski
# dla plikow Mirakl, ktore czesto maja dziesiatki/setki kolumn atrybutow -
# podnosimy go, zeby podglad z podswietleniem (zakladka 1) nie rzucal
# StreamlitAPIException przy wiekszych plikach.
pd.set_option("styler.render.max_elements", 5_000_000)


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

    st.success(f"Wczytano {len(products)} pozycji produktowych (format: {file_format}).")

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
        styled = build_styler(view_df, issues_by_row)
        st.dataframe(styled, use_container_width=True)

    with tabs[1]:
        df = products_to_dataframe(corrected)
        df = add_flag_column(df, summary_by_row)
        edited_df = st.data_editor(df, use_container_width=True, num_rows="fixed", key="editor")

    with tabs[2]:
        if all_issues:
            report_df = pd.DataFrame(all_issues)[
                ["line-number", "provider-unique-identifier", "attribute", "error_code", "severity", "message"]
            ]
            st.dataframe(report_df, use_container_width=True)
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
