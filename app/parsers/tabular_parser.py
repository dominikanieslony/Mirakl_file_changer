"""
Parser formatow CSV / XLSX.

Obserwacje z good_data_2.xlsx / good_data_5.xlsx:
- plik moze miec kilka arkuszy; tylko arkusz "Data" zawiera dane produktowe,
  pozostale ("ReferenceData", "Columns", "Error Details") to metadane/slowniki
  i sa ignorowane przy przetwarzaniu (ale kopiowane 1:1 do pliku wyjsciowego,
  zeby nic nie zgubic z oryginalnego szablonu).
- arkusz Data moze miec DWA wiersze naglowka: wiersz 1 = etykieta czytelna dla
  czlowieka (np. "Produktname"), wiersz 2 = kod techniczny (np. "ShortDescription_de").
  Wykrywamy to sprawdzajac, czy wartosci w drugim wierszu wygladaja jak znane
  kody atrybutow (camelCase / snake_case, bez spacji, brak polskich/niemieckich znakow).
- CSV (nie zaobserwowany w probkach, ale wymagany przez uzytkownika koncowego)
  zakladamy jako pojedynczy wiersz naglowka z kodami technicznymi - najprostszy,
  najbardziej prawdopodobny przypadek eksportu z systemu.
"""
from __future__ import annotations

import datetime
import re
from collections import OrderedDict
from dataclasses import dataclass, field

import openpyxl
import pandas as pd

AUX_SHEET_NAMES = {"referencedata", "columns", "error details", "errordetails"}


def _cell_to_str(val) -> str:
    """Rzutuje wartosc komorki XLSX (openpyxl zwraca natywne typy Pythona: int,
    float, datetime, bool...) na string, zeby wszystkie pola byly spojnie
    tekstowe - tak jak w parserze CSV (dtype=str) i XML (wartosci zawsze text).
    Mieszanie typow w jednej kolumnie DataFrame (np. str + float + datetime)
    psuje serializacje do Arrow przy renderowaniu w Streamlit."""
    if val is None:
        return ""
    if isinstance(val, float):
        return str(int(val)) if val.is_integer() else str(val)
    if isinstance(val, datetime.datetime):
        if val.time() == datetime.time(0, 0):
            return val.strftime("%Y-%m-%d")
        return val.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(val, datetime.date):
        return val.strftime("%Y-%m-%d")
    return str(val)


@dataclass
class TabularMeta:
    file_format: str  # "csv" | "xlsx"
    data_sheet_name: str | None = None
    has_two_row_header: bool = False
    label_row: list | None = None
    code_row: list | None = None
    other_sheets: dict = field(default_factory=dict)  # sheet_name -> openpyxl worksheet (do kopii)


def _looks_like_code(value) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or " " in v:
        return False
    # kody techniczne: litery/cyfry/podkreslenia, bez polskich/niemieckich znakow
    return bool(re.fullmatch(r"[A-Za-z0-9_]+", v))


def parse(path: str) -> tuple[list[OrderedDict], TabularMeta]:
    if path.lower().endswith(".csv"):
        return _parse_csv(path)
    return _parse_xlsx(path)


def _parse_csv(path: str) -> tuple[list[OrderedDict], TabularMeta]:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    codes = list(df.columns)
    products = [OrderedDict(zip(codes, row)) for row in df.itertuples(index=False, name=None)]
    meta = TabularMeta(file_format="csv", has_two_row_header=False, code_row=codes)
    return products, meta


def _parse_xlsx(path: str) -> tuple[list[OrderedDict], TabularMeta]:
    wb = openpyxl.load_workbook(path, data_only=True)
    sheet_names = wb.sheetnames

    data_sheet_name = None
    for name in sheet_names:
        if name.strip().lower() == "data":
            data_sheet_name = name
            break
    if data_sheet_name is None:
        # brak arkusza "Data" -> zakladamy, ze to prosty, jednoarkuszowy plik
        data_sheet_name = sheet_names[0]

    other_sheets = {
        name: wb[name] for name in sheet_names if name != data_sheet_name
    }

    ws = wb[data_sheet_name]
    rows_iter = ws.iter_rows(values_only=True)
    try:
        row1 = list(next(rows_iter))
    except StopIteration:
        return [], TabularMeta(file_format="xlsx", data_sheet_name=data_sheet_name, other_sheets=other_sheets)
    try:
        row2 = list(next(rows_iter))
    except StopIteration:
        row2 = None

    has_two_row_header = False
    if row2 is not None:
        code_like_count = sum(1 for v in row2 if _looks_like_code(v))
        if code_like_count >= max(1, int(0.6 * len([v for v in row2 if v is not None]))):
            has_two_row_header = True

    if has_two_row_header:
        label_row = row1
        code_row = row2
        data_rows = list(rows_iter)
    else:
        label_row = None
        code_row = row1
        data_rows = list(rows_iter) if row2 is None else [row2] + list(rows_iter)

    codes = [c if c is not None else f"col_{i}" for i, c in enumerate(code_row)]
    products = []
    for r in data_rows:
        if r is None or all(v is None for v in r):
            continue
        row = OrderedDict()
        for i, code in enumerate(codes):
            val = r[i] if i < len(r) else None
            row[code] = _cell_to_str(val)
        products.append(row)

    meta = TabularMeta(
        file_format="xlsx",
        data_sheet_name=data_sheet_name,
        has_two_row_header=has_two_row_header,
        label_row=label_row,
        code_row=code_row,
        other_sheets=other_sheets,
    )
    return products, meta


def serialize(products: list[OrderedDict], meta: TabularMeta, out_path: str) -> None:
    if meta.file_format == "csv":
        codes = meta.code_row or (list(products[0].keys()) if products else [])
        df = pd.DataFrame(products, columns=codes)
        df.to_csv(out_path, index=False)
        return

    wb = openpyxl.Workbook()
    default_sheet = wb.active
    wb.remove(default_sheet)

    ws = wb.create_sheet(meta.data_sheet_name or "Data")
    codes = meta.code_row or (list(products[0].keys()) if products else [])
    row_offset = 0
    if meta.has_two_row_header and meta.label_row:
        ws.append(meta.label_row)
        row_offset += 1
    ws.append(codes)
    row_offset += 1
    for row in products:
        ws.append([row.get(c, "") for c in codes])

    # kopiujemy arkusze pomocnicze 1:1, zeby nic nie zgubic z oryginalnego szablonu
    for name, src_ws in meta.other_sheets.items():
        dst_ws = wb.create_sheet(name)
        for r in src_ws.iter_rows(values_only=True):
            dst_ws.append(list(r))

    wb.save(out_path)
