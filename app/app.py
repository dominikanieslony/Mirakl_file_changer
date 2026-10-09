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
from logic.rules_engine import process_products, reference_values_from_rows
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

st.set_page_config(page_title="Product data correction", layout="wide")

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

# Docelowy budzet komorek (wiersze x kolumny) dla sugerowanego limitu wierszy -
# zakladka "Poprawione dane" (st.data_editor) renderuje WSZYSTKIE kolumny bez
# limitu jak podglad z podswietleniem, wiec dla bardzo szerokich plikow nawet
# nieduza liczba wierszy moze wyczerpac pamiec na Streamlit Cloud i zabic caly
# proces (bez tracebacku - "Error running app" bez sladu w logach).
CELL_BUDGET = 500_000

# Kolumny faktycznie analizowane/poprawiane przez rules_engine.py i validator.py
# (patrz *_CANDIDATES w rules_engine.py, KNOWN_ENUM_TOKENS w dictionaries.py oraz
# CORE_REQUIRED_FIELDS/GROUP_EXTRA_REQUIRED_FIELDS w field_requirements.py).
# Uzywane WYLACZNIE do zawezenia widoku w zakladce 'Podglad z podswietleniem'
# (tab 0) - dane wewnetrzne (przetwarzanie, edycja, eksport) zawsze zawieraja
# WSZYSTKIE oryginalne kolumny z pliku, bez zadnej utraty danych.
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


@st.cache_resource
def get_category_tree() -> CategoryTree:
    return CategoryTree.load(CATEGORIES_PATH)


def _reference_values(meta):
    """Dozwolone wartosci z arkusza ReferenceData szablonu (tylko XLSX) - patrz
    rules_engine.reference_values_from_rows."""
    other_sheets = getattr(meta, "other_sheets", None) or {}
    for name, rows in other_sheets.items():
        if name.strip().lower() == "referencedata":
            return reference_values_from_rows(rows)
    return None


def run_pipeline(products, category_tree, meta=None):
    corrected = []
    all_issues = []
    results = process_products(products, category_tree, _reference_values(meta))
    for idx, (fixed_row, issues) in enumerate(results):
        val_issues = validate_product(fixed_row, category_tree)
        issues = issues + val_issues
        sku = fixed_row.get("shop_sku") or fixed_row.get("Shop SKU") or f"row_{idx+1}"
        for iss in issues:
            all_issues.append({
                "line-number": idx + 1,
                "provider-unique-identifier": sku,
                **iss,
            })
        corrected.append(fixed_row)
    return corrected, all_issues


def main():
    st.title("Automatic product data correction")
    st.caption(
        "Upload a product data file (XML / CSV / XLSX / TXT). The app corrects the data "
        "according to the category guidelines and checks the category assignment "
        "against all_categories.json."
    )

    uploaded = st.file_uploader("Product data file", type=["xml", "csv", "xlsx", "xls", "txt"])
    if uploaded is None:
        st.info("Upload a file to start.")
        return

    suffix = os.path.splitext(uploaded.name)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded.getbuffer())
        tmp_path = tmp.name

    try:
        products, file_format, meta = loader.load(tmp_path)
    except Exception as e:
        st.error(f"Could not load the file: {e}")
        return

    st.success(f"Loaded {len(products)} products (format: {file_format}).")

    total_rows = len(products)
    total_cols = len(products[0]) if products else 0
    # Zakladka "Poprawione dane" (st.data_editor) nie ma zadnego limitu komorek
    # jak podglad z podswietleniem - dla plikow z duza liczba KOLUMN nawet
    # umiarkowana liczba wierszy potrafi przeciazyc pamiec na Streamlit Cloud i
    # zabic caly proces bez tracebacku (obserwowane - "Error running app" bez
    # sladu w logach). Sugerowany limit wierszy liczymy wiec z budzetu komorek
    # (wiersze x kolumny), a nie samej liczby wierszy.
    suggested_max_rows = min(MAX_ROWS_DEFAULT, max(1, CELL_BUDGET // max(total_cols, 1)))
    if total_rows > suggested_max_rows:
        st.warning(
            f"The file has {total_rows} products x {total_cols} columns - processing "
            f"and previewing this much data at once may exceed the app's memory "
            f"and crash it. You can limit the number of processed rows below "
            f"(e.g. process the file in batches) - 0 means all rows, but with "
            f"a file this large the app may crash."
        )
    max_rows = st.number_input(
        "Maximum number of rows to process (0 = all)",
        min_value=0,
        value=suggested_max_rows if total_rows > suggested_max_rows else 0,
        step=100,
    )
    if max_rows and max_rows < total_rows:
        products = products[:max_rows]
        st.info(f"Limited to the first {max_rows} of {total_rows} rows.")

    if "processed" not in st.session_state:
        st.session_state.processed = False

    if st.button("Process and correct data", type="primary"):
        category_tree = get_category_tree()
        with st.spinner("Analyzing and correcting data..."):
            corrected, all_issues = run_pipeline(products, category_tree, meta)
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
    col1.metric("Products", len(corrected))
    col2.metric("Automatic fixes", len(auto_fixed))
    col3.metric("Manual review", len(manual_review))

    if manual_review:
        st.warning(
            f"Note: {len(manual_review)} item(s) may contain an error that could not "
            f"be resolved automatically. Check the 'Highlighted preview' tab "
            f"(fields in red = to check) and, if needed, fix the data manually in "
            f"the 'Corrected data' tab before downloading the file."
        )

    tabs = st.tabs(["Highlighted preview", "Corrected data (editable)", "Issue report"])

    issues_by_row, summary_by_row = build_issue_maps(all_issues)

    with tabs[0]:
        st.caption(
            "Cells with red text are the specific fields that need manual review. "
            "The 'Fields to check' column summarizes them per row. For performance, "
            "this preview shows only the columns the app actually analyzes - the "
            "downloadable file ('Corrected data' tab) contains ALL original columns. "
            "This view is read-only - make edits in the 'Corrected data' tab."
        )
        preview_df = products_to_dataframe(corrected)
        relevant_present = [c for c in preview_df.columns if c in RELEVANT_COLUMNS]
        if relevant_present:
            preview_df = preview_df[relevant_present]
        preview_df = add_flag_column(preview_df, summary_by_row)
        if manual_review:
            only_flagged = st.checkbox("Show only rows that need review", value=True)
            view_df = preview_df[preview_df[FLAG_COLUMN] != ""] if only_flagged else preview_df
        else:
            view_df = preview_df
        if view_df.size > MAX_STYLED_CELLS:
            st.info(
                f"The view has {view_df.size} cells - field highlighting was "
                f"skipped for performance. Narrow the view with the checkbox "
                f"above or check the 'Issue report' tab to see the specific "
                f"errors."
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
            st.info("No issues found.")

    st.divider()
    out_name = f"corrected_{uploaded.name}"
    if st.button("Prepare file for download"):
        final_products = dataframe_to_products(edited_df)
        out_path = os.path.join(tempfile.gettempdir(), out_name)
        loader.save(final_products, st.session_state.file_format, st.session_state.meta, out_path)
        with open(out_path, "rb") as f:
            st.download_button(
                "Download corrected file",
                data=f.read(),
                file_name=out_name,
            )


if __name__ == "__main__":
    main()
