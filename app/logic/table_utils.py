"""
Funkcje pomocnicze do konwersji produktow <-> DataFrame oraz budowy podswietlenia
pol wymagajacych recznej weryfikacji. Celowo bez zaleznosci od streamlit, zeby
dalo sie je przetestowac niezaleznie od uruchomionej aplikacji.
"""
from __future__ import annotations

from collections import OrderedDict

import pandas as pd

FLAG_COLUMN = "⚠ Pola do sprawdzenia"
CELL_HIGHLIGHT_STYLE = "background-color: #fff3b0"
FLAG_CELL_HIGHLIGHT_STYLE = "background-color: #ffd166; font-weight: 600"


def products_to_dataframe(products: list[OrderedDict]) -> pd.DataFrame:
    if not products:
        return pd.DataFrame()
    # zachowaj kolejnosc kolumn z pierwszego produktu, dodaj brakujace z pozostalych
    columns = list(products[0].keys())
    seen = set(columns)
    for row in products[1:]:
        for k in row.keys():
            if k not in seen:
                columns.append(k)
                seen.add(k)
    return pd.DataFrame(products, columns=columns)


def dataframe_to_products(df: pd.DataFrame) -> list[OrderedDict]:
    df = df.drop(columns=[FLAG_COLUMN], errors="ignore")
    records = df.to_dict(orient="records")
    return [OrderedDict((k, "" if pd.isna(v) else v) for k, v in r.items()) for r in records]


def build_issue_maps(all_issues: list[dict]) -> tuple[dict[int, dict[str, list[str]]], dict[int, str]]:
    """Zwraca:
    - issues_by_row: {indeks_wiersza(0-based): {kod_pola: [komunikaty]}} - tylko manual_review
    - summary_by_row: {indeks_wiersza: krotki tekst do kolumny FLAG_COLUMN}
    """
    issues_by_row: dict[int, dict[str, list[str]]] = {}
    for iss in all_issues:
        if iss["severity"] != "manual_review":
            continue
        row_idx = iss["line-number"] - 1
        attr = iss["attribute"]
        issues_by_row.setdefault(row_idx, {}).setdefault(attr, []).append(iss["message"])

    summary_by_row = {
        row_idx: ", ".join(sorted(attrs.keys()))
        for row_idx, attrs in issues_by_row.items()
    }
    return issues_by_row, summary_by_row


def add_flag_column(df: pd.DataFrame, summary_by_row: dict[int, str]) -> pd.DataFrame:
    df = df.reset_index(drop=True)
    df.insert(0, FLAG_COLUMN, [summary_by_row.get(i, "") for i in df.index])
    return df


def build_styler(df: pd.DataFrame, issues_by_row: dict[int, dict[str, list[str]]]):
    """Zwraca pandas Styler podswietlajacy dokladnie te komorki, ktore maja problem
    do recznej weryfikacji, plus kolumne FLAG_COLUMN dla oflagowanych wierszy."""
    style_df = pd.DataFrame("", index=df.index, columns=df.columns)
    for row_idx, attrs in issues_by_row.items():
        if row_idx not in style_df.index:
            continue
        for attr in attrs:
            if attr in style_df.columns:
                style_df.loc[row_idx, attr] = CELL_HIGHLIGHT_STYLE
        if FLAG_COLUMN in style_df.columns:
            style_df.loc[row_idx, FLAG_COLUMN] = FLAG_CELL_HIGHLIGHT_STYLE
    return df.style.apply(lambda _: style_df, axis=None)
