"""
Parser formatu XML zaobserwowanego we wszystkich plikach *.xml z good_data:

<import xmlns:xsi="..." xmlns:xsd="...">
  <products>
    <product>
      <attribute><code>shop_sku</code><value>...</value></attribute>
      ...
    </product>
  </products>
</import>

Kazdy <product> staje sie slownikiem {code: value}. Kolejnosc atrybutow jest
zachowywana (potrzebna do wiernego odtworzenia pliku przy eksporcie).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import OrderedDict

NS = {
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "xsd": "http://www.w3.org/2001/XMLSchema",
}


def parse(path: str) -> list[OrderedDict]:
    ET.register_namespace("xsi", NS["xsi"])
    ET.register_namespace("xsd", NS["xsd"])
    tree = ET.parse(path)
    root = tree.getroot()
    products_el = root.find("products")
    if products_el is None:
        raise ValueError("Nieprawidlowy format XML: brak elementu <products>")

    products = []
    for product_el in products_el.findall("product"):
        row = OrderedDict()
        for attr_el in product_el.findall("attribute"):
            code_el = attr_el.find("code")
            value_el = attr_el.find("value")
            code = code_el.text if code_el is not None else None
            value = value_el.text if value_el is not None else None
            if code is not None:
                row[code] = value if value is not None else ""
        products.append(row)
    return products


def serialize(products: list[OrderedDict], out_path: str) -> None:
    ET.register_namespace("xsi", NS["xsi"])
    ET.register_namespace("xsd", NS["xsd"])
    root = ET.Element(
        "import",
        {
            "xmlns:xsi": NS["xsi"],
            "xmlns:xsd": NS["xsd"],
        },
    )
    products_el = ET.SubElement(root, "products")
    for row in products:
        product_el = ET.SubElement(products_el, "product")
        for code, value in row.items():
            attr_el = ET.SubElement(product_el, "attribute")
            code_el = ET.SubElement(attr_el, "code")
            code_el.text = code
            value_el = ET.SubElement(attr_el, "value")
            value_el.text = "" if value is None else str(value)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
