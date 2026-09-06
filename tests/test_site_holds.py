"""A hold is how the host CLI tells the worker to stop claiming for one Site while a stable
window is open; the worker keeps heartbeating so users see a queue, not a 503."""
import json

from dsherp import site_holds


def test_a_hold_is_one_file_per_site_and_release_removes_only_that_one(tmp_path):
    a = site_holds.hold(tmp_path, "acme.tenant.example.com", "backup")
    site_holds.hold(tmp_path, "beta.tenant.example.com", "release")
    assert a == tmp_path / "holds" / "acme.tenant.example.com" and json.loads(a.read_text())["reason"] == "backup"
    assert site_holds.held(tmp_path) == {"acme.tenant.example.com", "beta.tenant.example.com"}
    site_holds.release(tmp_path, "acme.tenant.example.com")
    assert site_holds.held(tmp_path) == {"beta.tenant.example.com"}
    site_holds.release(tmp_path, "acme.tenant.example.com")  # idempotent
    assert (tmp_path / "holds").stat().st_mode & 0o077 == 0
    assert site_holds.held(tmp_path / "nowhere") == set()


def test_an_unreadable_hold_directory_is_reported_not_treated_as_no_holds(tmp_path):
    """A hold that cannot be read must not silently become 'claim away': the caller decides."""
    (tmp_path / "holds").mkdir()
    (tmp_path / "holds").chmod(0o000)
    try:
        assert site_holds.held(tmp_path, on_error="raise") is not None
    except OSError:
        pass
    else:
        raise AssertionError("an unreadable hold directory must raise when asked to")
    finally:
        (tmp_path / "holds").chmod(0o700)
