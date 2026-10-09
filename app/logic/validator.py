from __future__ import annotations

from collections import OrderedDict

from . import field_requirements as FR
from .categories import CategoryTree
from .rules_engine import _first_present, DESC_CODE_CANDIDATES, CATEGORY_CODE_CANDIDATES


def validate_product(row: OrderedDict, category_tree: CategoryTree) -> list[dict]:
    issues = []
    sku = row.get("shop_sku") or row.get("Shop SKU") or "?"

    cat_field = _first_present(row, CATEGORY_CODE_CANDIDATES)
    resolved_code = category_tree.resolve(row.get(cat_field)) if cat_field else None
    path_de = category_tree.path_de(resolved_code) if resolved_code else None
    group = FR.detect_group(path_de)

    required = FR.required_fields_for(group)
    for field_code in required:
        val = row.get(field_code)
        if val is None or str(val).strip() == "":
            issues.append({
                "attribute": field_code,
                "error_code": "REQUIRED_FIELD_MISSING",
                "message": f"Required field '{field_code}' is empty for category "
                           f"'{path_de or '?'}'.",
                "severity": "manual_review",
            })

    if group in FR.GROUP_DESCRIPTION_SHOULD_MENTION:
        desc_field = _first_present(row, DESC_CODE_CANDIDATES)
        desc_text = str(row.get(desc_field, "") or "").lower() if desc_field else ""
        expected_terms = FR.GROUP_DESCRIPTION_SHOULD_MENTION[group]
        if desc_text and not any(term in desc_text for term in expected_terms):
            issues.append({
                "attribute": desc_field or "LongDescription_de",
                "error_code": "DESCRIPTION_MISSING_EXPECTED_INFO",
                "message": (f"The description mentions none of the expected aspects "
                            f"{expected_terms} typical for this product group - "
                            f"needs manual review (content is not added automatically)."),
                "severity": "manual_review",
            })

    return issues
