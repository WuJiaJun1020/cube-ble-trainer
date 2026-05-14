import json
from pathlib import Path


HISTORY_PATH = Path("data") / "timing_history.json"


def load_history():
    try:
        if not HISTORY_PATH.exists():
            return []

        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))

        if isinstance(data, list):
            return data

    except Exception:
        pass

    return []


def save_history(records):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_record(record, max_records=500):
    records = load_history()
    records.insert(0, record)
    records = records[:max_records]
    save_history(records)
    return records
