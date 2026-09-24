from __future__ import annotations

from . import tabular_parser, xml_parser


def _looks_like_xml(path: str) -> bool:
    """Sprawdza pierwsze znaki pliku, zeby odroznic .txt zawierajacy XML
    (zaobserwowane - plik .txt z ta sama struktura co .xml, tylko inne
    rozszerzenie) od .txt z danymi tabelarycznymi (CSV/TSV)."""
    with open(path, encoding="utf-8", errors="ignore") as f:
        head = f.read(200).lstrip()
    return head.startswith("<?xml") or head.startswith("<import")


def load(path: str):
    """Zwraca (products, format, meta) gdzie meta jest None dla XML (nie potrzebne
    do serializacji - struktura jest stala) albo TabularMeta dla CSV/XLSX/TXT."""
    lower = path.lower()
    if lower.endswith(".xml"):
        products = xml_parser.parse(path)
        return products, "xml", None
    if lower.endswith(".txt"):
        if _looks_like_xml(path):
            products = xml_parser.parse(path)
            return products, "xml", None
        products, meta = tabular_parser.parse_txt(path)
        return products, meta.file_format, meta
    if lower.endswith((".xlsx", ".xls", ".csv")):
        products, meta = tabular_parser.parse(path)
        return products, meta.file_format, meta
    raise ValueError(f"Nieobslugiwany format pliku: {path}")


def save(products, file_format: str, meta, out_path: str) -> None:
    if file_format == "xml":
        xml_parser.serialize(products, out_path)
    else:
        tabular_parser.serialize(products, meta, out_path)
