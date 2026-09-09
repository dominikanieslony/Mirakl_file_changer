from __future__ import annotations

from . import tabular_parser, xml_parser


def load(path: str):
    """Zwraca (products, format, meta) gdzie meta jest None dla XML (nie potrzebne
    do serializacji - struktura jest stala) albo TabularMeta dla CSV/XLSX."""
    lower = path.lower()
    if lower.endswith(".xml"):
        products = xml_parser.parse(path)
        return products, "xml", None
    if lower.endswith((".xlsx", ".xls", ".csv")):
        products, meta = tabular_parser.parse(path)
        return products, meta.file_format, meta
    raise ValueError(f"Nieobslugiwany format pliku: {path}")


def save(products, file_format: str, meta, out_path: str) -> None:
    if file_format == "xml":
        xml_parser.serialize(products, out_path)
    else:
        tabular_parser.serialize(products, meta, out_path)
