import json
from pathlib import Path

_LIBRARY_FILE = Path(__file__).with_name("cfop_library.json")


def _normalize_item(item, idx=0):
    algorithms = [str(a).strip() for a in item.get("algorithms", []) if str(a).strip()]
    if not algorithms:
        return None
    return {
        "id": item.get("id") or f"cfop_{idx + 1:03d}",
        "category": item.get("category", "CFOP"),
        "case_id": item.get("case_id") or item.get("name") or f"Case {idx + 1}",
        "name": item.get("name") or item.get("case_id") or f"Case {idx + 1}",
        "slot": item.get("slot", ""),
        "algorithms": algorithms,
    }


def load_cfop_library():
    """Load bundled CFOP formulas.

    The JSON layout is intentionally CRUD-friendly: each item has category,
    case_id/name, optional slot metadata, and a list of alternative algorithms.
    UI editing writes back to the same JSON file in this desktop project.
    """
    try:
        data = json.loads(_LIBRARY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

    result = []
    for idx, item in enumerate(data):
        normalized = _normalize_item(item, idx)
        if normalized:
            result.append(normalized)
    return result


def save_cfop_library(items):
    """Save CFOP formulas back to the bundled JSON library."""
    data = []
    for idx, item in enumerate(items or []):
        normalized = _normalize_item(item, idx)
        if normalized:
            data.append(normalized)

    _LIBRARY_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data
