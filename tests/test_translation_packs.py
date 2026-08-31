import csv
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "infra/build_translation_packs.py"


def load_builder():
    assert SCRIPT.exists(), "infra/build_translation_packs.py is missing"
    spec = importlib.util.spec_from_file_location("build_translation_packs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_glossary(root, rows, header=("source", "context", "zh", "note")):
    path = root / "config/terminology/glossary.csv"
    path.parent.mkdir(parents=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def test_repository_glossary_generates_valid_zh_pack_without_state_machine_actions(tmp_path):
    builder = load_builder()
    source = ROOT / "config/terminology/glossary.csv"
    target = tmp_path / "config/terminology/glossary.csv"
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())

    outputs = builder.build_translation_packs(tmp_path)

    assert outputs == (tmp_path / "frappe_app/dsherp_bridge/translations/zh.csv",)
    with outputs[0].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 50
    assert all(len(row) == 3 and row[0] and row[1] for row in rows)
    assert not {"Submit", "Cancel"}.intersection(row[0] for row in rows)
    assert [row[:3] for row in rows if row[0] == "General Ledger"] == [
        ["General Ledger", "总账", ""],
        ["General Ledger", "总账", "Warehouse"],
    ]


@pytest.mark.parametrize(
    ("header", "rows", "message"),
    [
        (("source", "zh"), [("Item", "物料")], "header"),
        (
            ("source", "context", "zh", "note"),
            [("Item", "", "物料", "first"), ("Item", "", "物料", "duplicate")],
            "Duplicate terminology key: Item",
        ),
        (
            ("source", "context", "zh", "note"),
            [("Submit", "", "提交", "workflow action")],
            "Forbidden state-machine term: Submit",
        ),
        (
            ("source", "context", "zh", "note"),
            [("Cancel", "", "取消", "workflow action")],
            "Forbidden state-machine term: Cancel",
        ),
    ],
)
def test_invalid_glossary_fails_fast(tmp_path, header, rows, message):
    builder = load_builder()
    write_glossary(tmp_path, rows, header)

    with pytest.raises(SystemExit, match=message):
        builder.build_translation_packs(tmp_path)


def test_build_is_sorted_utf8_and_idempotent(tmp_path):
    builder = load_builder()
    write_glossary(
        tmp_path,
        [
            ("Trial Balance", "", "科目余额表", "financial report"),
            ("General Ledger", "Warehouse", "总账", "warehouse button"),
            ("General Ledger", "", "总账", "financial report"),
        ],
    )

    (output,) = builder.build_translation_packs(tmp_path)
    first = output.read_bytes()
    builder.build_translation_packs(tmp_path)

    assert output.read_bytes() == first
    assert first == (
        "General Ledger,总账,\n"
        "General Ledger,总账,Warehouse\n"
        "Trial Balance,科目余额表,\n"
    ).encode()
    assert b"\\u" not in first


def test_repository_translation_pack_matches_generated_output(tmp_path):
    builder = load_builder()
    glossary = tmp_path / "config/terminology/glossary.csv"
    glossary.parent.mkdir(parents=True)
    glossary.write_bytes((ROOT / "config/terminology/glossary.csv").read_bytes())
    (generated,) = builder.build_translation_packs(tmp_path)
    committed = ROOT / "frappe_app/dsherp_bridge/translations/zh.csv"

    assert committed.exists(), "committed zh.csv is missing"
    assert committed.read_bytes() == generated.read_bytes()
