"""Command boundary tests without network or real account details."""

import json

import pytest

from egeders_downloader import cli, service
from egeders_downloader.models import Activity, AuthExpiredError, Course, Summary


@pytest.fixture
def configured(tmp_path):
    settings = service.Settings(tmp_path)
    settings.save(
        {
            "account": "test@ogrenci.ege.edu.tr",
            "output": str(tmp_path / "output"),
            "selected": {"test@ogrenci.ege.edu.tr": [123]},
        }
    )
    return ["--state-dir", str(tmp_path)]


def test_sync_saved_selection_json(configured, monkeypatch, capsys):
    calls = []

    def sync(account, state_dir, output, course_ids, log):
        calls.append((account.key, course_ids))
        log("progress")
        return Summary(new=1, unchanged=2)

    monkeypatch.setattr(service, "sync", sync)
    assert cli.main([*configured, "sync", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["new"] == 1
    assert captured.err.strip() == "progress"
    assert calls == [("test@ogrenci.ege.edu.tr", [123])]


def test_sync_convert_pptx_flag_and_conversion_failure_exit_zero(configured, monkeypatch, capsys):
    calls = []

    def sync(account, state_dir, output, course_ids, log, convert_pptx=False):
        calls.append((course_ids, convert_pptx))
        return Summary(new=1, converted=0, conversion_failed=1, errors=["Conversion failed for resource 1"])

    monkeypatch.setattr(service, "sync", sync)
    assert cli.main([*configured, "sync", "--convert-pptx", "--json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["new"] == 1
    assert payload["conversion_failed"] == 1
    assert calls == [([123], True)]


def test_inventory_json_and_text(configured, monkeypatch, capsys):
    sample = [
        Activity(
            course_id=123,
            id=10,
            title="Week 1.pptx",
            section="Week 1",
            kind="resource",
            type_label="File (PPTX)",
            url="https://egeders.ege.edu.tr/mod/resource/view.php?id=10",
            downloadable=True,
            status="Downloadable file resource (PPT/PPTX → PDF supported)",
            course_title="Economics",
        ),
        Activity(
            course_id=123,
            id=11,
            title="Midterm Quiz",
            section="Week 2",
            kind="quiz",
            type_label="Quiz",
            url="https://egeders.ege.edu.tr/mod/quiz/view.php?id=11",
            downloadable=False,
            status="Unsupported quiz (not downloaded or followed)",
            course_title="Economics",
        ),
    ]
    monkeypatch.setattr(service, "inventory", lambda account, state_dir, course_ids, log: sample)
    assert cli.main([*configured, "inventory", "--json"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert len(parsed) == 2
    assert parsed[0]["type_label"] == "File (PPTX)"
    assert parsed[1]["kind"] == "quiz"
    assert not parsed[1]["downloadable"]

    assert cli.main([*configured, "inventory", "--course", "123"]) == 0
    text_out = capsys.readouterr().out
    assert "Week 1.pptx" in text_out
    assert "Midterm Quiz" in text_out
    assert "https://egeders.ege.edu.tr/mod/quiz/view.php?id=11" in text_out


def test_expired_session_exit_two(configured, monkeypatch, capsys):
    def discover(*args, **kwargs):
        raise AuthExpiredError("expired")

    monkeypatch.setattr(service, "discover", discover)
    assert cli.main([*configured, "courses"]) == 2
    assert "login --manual" in capsys.readouterr().err


def test_missing_selection_does_not_sync(configured, monkeypatch, capsys):
    settings = service.Settings(configured[1])
    settings.save({"account": "test@ogrenci.ege.edu.tr"})

    def forbidden(*args, **kwargs):
        pytest.fail("sync should not run without a course selection")

    monkeypatch.setattr(service, "sync", forbidden)
    assert cli.main([*configured, "sync"]) == 1
    assert "No courses selected" in capsys.readouterr().err


def test_failed_sync_exit_one(configured, monkeypatch):
    monkeypatch.setattr(
        service,
        "sync",
        lambda *args, **kwargs: Summary(failed=1, errors=["file failed"]),
    )
    assert cli.main([*configured, "sync"]) == 1


def test_explicit_courses_override_saved(configured, monkeypatch):
    calls = []

    def sync(account, state_dir, output, course_ids, log):
        calls.append(course_ids)
        return Summary()

    monkeypatch.setattr(service, "sync", sync)
    assert cli.main([*configured, "sync", "--course", "9", "10", "--course", "11"]) == 0
    assert calls == [[9, 10, 11]]


def test_courses_json(configured, monkeypatch, capsys):
    monkeypatch.setattr(
        service,
        "discover",
        lambda *args, **kwargs: [
            Course(5, "Example", "https://example.invalid", "2627G")
        ],
    )
    assert cli.main([*configured, "courses", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == 5


def test_login_consumes_environment_password(configured, monkeypatch):
    calls = []
    monkeypatch.setenv("EGEDERS_PASSWORD", "temporary-test-value")
    monkeypatch.setattr(
        service, "login", lambda *args, **kwargs: calls.append(kwargs["password"])
    )
    assert cli.main([*configured, "login"]) == 0
    assert calls == ["temporary-test-value"]
    assert "EGEDERS_PASSWORD" not in cli.os.environ
