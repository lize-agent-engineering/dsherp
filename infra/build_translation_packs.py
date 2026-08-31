"""Build deterministic Frappe translation CSV files from the terminology glossary."""

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HEADER = ("source", "context", "zh", "note")
FORBIDDEN_SOURCES = {"Submit", "Cancel"}


def _read_glossary(path):
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = tuple(next(reader, ()))
        if header != HEADER:
            raise SystemExit("Invalid glossary header: expected source,context,zh,note")

        rows = []
        keys = set()
        for line_number, row in enumerate(reader, 2):
            if len(row) != len(HEADER):
                raise SystemExit(f"Invalid glossary row {line_number}: expected 4 columns")
            source, context, translated, note = row
            if any(value != value.strip() for value in row):
                raise SystemExit(f"Invalid glossary row {line_number}: surrounding whitespace")
            if not source or not translated or not note:
                raise SystemExit(f"Invalid glossary row {line_number}: required value is empty")
            if source in FORBIDDEN_SOURCES:
                raise SystemExit(f"Forbidden state-machine term: {source}")
            key = (source, context)
            if key in keys:
                label = source if not context else f"{source}:{context}"
                raise SystemExit(f"Duplicate terminology key: {label}")
            keys.add(key)
            rows.append((source, translated, context))
    return sorted(rows, key=lambda row: (row[0], row[2]))


def build_translation_packs(root=ROOT):
    glossary = root / "config/terminology/glossary.csv"
    rows = _read_glossary(glossary)
    output = root / "frappe_app/dsherp_bridge/translations/zh.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows(rows)
    return (output,)


if __name__ == "__main__":
    build_translation_packs()
