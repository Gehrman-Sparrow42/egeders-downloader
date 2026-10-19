"""Local persistence and process-lock behavior without opening a browser."""

import pytest

from egeders_downloader.models import Account
from egeders_downloader.service import Settings, account_lock


def test_settings_roundtrip_and_replace(tmp_path):
    settings = Settings(tmp_path / "state")
    assert settings.load() == {}
    data = {"account": "student@ogrenci.ege.edu.tr", "courses": [178423], "output": "Ders İçerikleri"}
    settings.save(data)
    assert settings.load() == data
    settings.save({"courses": []})
    assert settings.load() == {"courses": []}
    assert not list(settings.state_dir.glob("*.tmp"))


@pytest.mark.parametrize("contents", ['{"courses":', "[]", "null", "123"])
def test_settings_rejects_corrupt_or_non_object_json(tmp_path, contents):
    settings = Settings(tmp_path)
    (tmp_path / "settings.json").write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot read settings|JSON object"):
        settings.load()
    assert (tmp_path / "settings.json").read_text(encoding="utf-8") == contents


def test_normalized_account_identity_and_domain_isolation():
    first = Account("  STUDENT  ")
    second = Account("Student@OGRENCI.EGE.EDU.TR")
    assert first == second
    assert first.key == "student@ogrenci.ege.edu.tr"
    assert first.slug == second.slug
    assert first.slug != Account("student", "ege.edu.tr").slug


def test_account_lock_contention_isolation_and_release(tmp_path):
    student = Account("student")
    other = Account("other")
    with account_lock(tmp_path, student):
        with pytest.raises(RuntimeError, match="Another operation"), account_lock(tmp_path, Account("STUDENT")):
            pytest.fail("Concurrent access to the same normalized account was allowed.")
        with account_lock(tmp_path, other):
            pass
    with account_lock(tmp_path, student):
        pass


def test_account_lock_releases_after_operation_exception(tmp_path):
    account = Account("student")
    with pytest.raises(ValueError, match="operation failed"), account_lock(tmp_path, account):
        raise ValueError("operation failed")
    with account_lock(tmp_path, account):
        pass
