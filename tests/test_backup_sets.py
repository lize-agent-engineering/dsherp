"""A backup set is one stable state of one Site: three data pieces and one secret piece,
bound by one id that appears in both halves and in both repositories."""
import json

import pytest

from dsherp import backup_sets


REAL_SITES = ("dsherp-validation.localhost", "dsherp-daily.localhost", "acme.tenant.example.com",
              "dsherp-platform.localhost", "platform.tenant.example.com")


def test_set_ids_carry_the_window_stamp_the_site_slug_and_a_token_and_parse_back():
    set_id = backup_sets.new_set_id("acme.tenant.example.com", "20260906_020007", "k3f9qx")
    assert set_id == "20260906_020007-acme_tenant_example_com-k3f9qx"
    assert backup_sets.parse_set_id(set_id) == {"stamp": "20260906_020007", "slug": "acme_tenant_example_com", "token": "k3f9qx"}
    assert backup_sets.parse_set_id("../evil") is None and backup_sets.parse_set_id("") is None
    assert backup_sets.parse_set_id("20260906_020007-acme-k3f9q") is None      # token too short
    assert backup_sets.parse_set_id("2026090_020007-acme-k3f9qx") is None      # stamp not a stamp
    with pytest.raises(ValueError):
        backup_sets.new_set_id("acme.tenant.example.com", "2026-09-06", "k3f9qx")
    with pytest.raises(ValueError):
        backup_sets.new_set_id("acme.tenant.example.com", "20260906_020007", "K3F9QX")
    with pytest.raises(ValueError):
        backup_sets.new_set_id("bad site/name", "20260906_020007", "k3f9qx")


def test_every_site_this_project_actually_runs_survives_the_round_trip():
    """Review round 2: the slug must keep hyphens; dsherp-validation.localhost is a real site."""
    for site in REAL_SITES:
        set_id = backup_sets.new_set_id(site, "20260906_020007", "k3f9qx")
        parsed = backup_sets.parse_set_id(set_id)
        assert parsed is not None, site
        assert parsed["slug"] == backup_sets.slug(site) and parsed["stamp"] == "20260906_020007" and parsed["token"] == "k3f9qx"
    assert backup_sets.slug("dsherp-validation.localhost") == "dsherp-validation_localhost"


def _set_doc(**over):
    base = dict(set_id="20260906_020007-acme_tenant_example_com-k3f9qx", site="acme.tenant.example.com", kind="scheduled",
                stamp="20260906_020007", window={"started": "2026-09-06T02:00:07Z", "finished": "2026-09-06T02:00:41Z"},
                image_tag="v0.3.1-rc2", image_id="sha256:c914", frappe_version="16.31.0",
                pieces={"database.sql.gz": {"sha256": "a" * 64, "bytes": 10}, "files.tar": {"sha256": "b" * 64, "bytes": 20},
                        "private-files.tar": {"sha256": "c" * 64, "bytes": 30}},
                snapshot_sha256="d" * 64)
    return backup_sets.set_manifest(**{**base, **over})


def test_the_set_manifest_records_every_data_piece_and_refuses_a_missing_or_extra_one():
    doc = _set_doc()
    assert doc["format"] == 1 and doc["kind"] == "scheduled" and set(doc["pieces"]) == set(backup_sets.DATA_PIECES)
    with pytest.raises(ValueError):
        _set_doc(pieces={"database.sql.gz": {"sha256": "a" * 64, "bytes": 1}})
    with pytest.raises(ValueError):
        _set_doc(kind="weekly")
    with pytest.raises(ValueError):
        _set_doc(pieces={**_set_doc()["pieces"], "site_config_backup.json": {"sha256": "e" * 64, "bytes": 1}})
    with pytest.raises(ValueError):
        _set_doc(image_id="")            # an unverifiable artefact identity is not a backup set
    with pytest.raises(ValueError):
        _set_doc(image_tag="")
    with pytest.raises(ValueError):
        _set_doc(snapshot_sha256="nope")


def test_the_pair_manifest_binds_the_secret_half_to_the_data_half_by_id_and_by_every_digest():
    doc = _set_doc()
    pair = backup_sets.pair_manifest(doc, config_sha256="e" * 64)
    assert pair["set_id"] == doc["set_id"] and pair["site"] == doc["site"] and pair["format"] == 1
    assert pair["pieces"] == {name: row["sha256"] for name, row in doc["pieces"].items()}
    assert pair["set_sha256"] == backup_sets.set_sha256(doc) and pair["config_sha256"] == "e" * 64
    assert pair["snapshot_sha256"] == doc["snapshot_sha256"]
    assert backup_sets.pair_matches(doc, pair)
    assert not backup_sets.pair_matches(_set_doc(snapshot_sha256="f" * 64), pair)
    assert not backup_sets.pair_matches(doc, {**pair, "set_id": "20260906_020007-acme_tenant_example_com-other0"})
    assert not backup_sets.pair_matches(doc, {**pair, "pieces": {**pair["pieces"], "files.tar": "0" * 64}})
    assert not backup_sets.pair_matches(doc, {**pair, "set_sha256": "0" * 64})
    assert not backup_sets.pair_matches(doc, {**pair, "format": 0})
    assert not backup_sets.pair_matches(doc, {})
    with pytest.raises(ValueError):
        backup_sets.pair_manifest(doc, config_sha256="short")
    assert json.dumps(doc, sort_keys=True) == json.dumps(json.loads(json.dumps(doc)), sort_keys=True)


def test_digests_are_defined_over_exactly_the_bytes_the_restore_will_read():
    doc = _set_doc()
    text = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    import hashlib
    assert backup_sets.set_sha256(doc) == hashlib.sha256(text.encode()).hexdigest()
    snapshot = {"format": 1, "tables": {}, "singles": {"System Settings": {"country": "中国"}}}
    body = backup_sets.snapshot_text(snapshot)
    assert body == json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    assert backup_sets.snapshot_sha256(snapshot) == hashlib.sha256(body.encode()).hexdigest()
    assert "中国" in body, "the digest is over the same UTF-8 text the file holds"


def test_local_pruning_keeps_the_newest_three_and_never_the_protected_ones():
    ids = [f"2026090{d}_020000-acme_tenant_example_com-aaaaaa" for d in range(1, 7)]
    assert backup_sets.local_prune(ids, keep=3) == ids[:3]
    assert backup_sets.local_prune(ids, keep=3, protect=(ids[0],)) == ids[1:3]
    assert backup_sets.local_prune(ids[:2], keep=3) == []
    assert backup_sets.local_prune(list(reversed(ids)), keep=3) == ids[:3]
    assert backup_sets.local_prune([*ids, "not-a-set-id"], keep=3) == ids[:3]
