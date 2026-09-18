import pytest

from pne_scheduler.io.atomic_output import output_lock, require_distinct_paths, staged_path


def test_sidecar_cannot_be_published(tmp_path):
    lock = tmp_path / ".x.lock"
    with output_lock(tmp_path / "x"):
        original = lock.read_bytes()
        with pytest.raises(ValueError, match="Reserved"):
            with output_lock(lock):
                pytest.fail("sidecar writer entered")
        with pytest.raises(ValueError, match="Reserved"):
            with staged_path(lock):
                pytest.fail("sidecar staging entered")
        assert lock.read_bytes() == original
    assert not lock.exists()


@pytest.mark.parametrize("name", [".x.lock", ".manifest.LOCK", ".x.lock/child"])
def test_preflight_reserves_all_user_paths(tmp_path, name):
    with pytest.raises(ValueError, match="Reserved"):
        require_distinct_paths(tmp_path / "source", tmp_path / "data", tmp_path / "out", tmp_path / name)
    assert list(tmp_path.iterdir()) == []


def test_cleanup_preserves_replacement_lock(tmp_path):
    lock = tmp_path / ".x.lock"
    with output_lock(tmp_path / "x"):
        lock.unlink()
        lock.write_bytes(b"other owner")
    assert lock.read_bytes() == b"other owner"


def test_source_sidecar_rejected_before_creation(tmp_path):
    source = tmp_path / ".x.lock"
    source.write_bytes(b"source data")
    with pytest.raises(ValueError, match="Reserved"):
        with output_lock(tmp_path / "x", source):
            pytest.fail("source alias accepted")
    assert source.read_bytes() == b"source data"