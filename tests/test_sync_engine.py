import hashlib
import tempfile
import threading
from pathlib import Path

import pytest

from egeders_downloader.manifest import Manifest
from egeders_downloader.models import Account, AuthExpiredError, Course, Download
from egeders_downloader.sync_engine import sync_courses

COURSE = Course(
    178423,
    "Engineering Economics 2627G",
    "https://egeders.ege.edu.tr/course/view.php?id=178423",
)
HTML = """<li class="section"><h3 class="sectionname">Introduction</h3>
<li class="activity"><a href="/mod/resource/view.php?id=1342714">Introduction</a></li></li>"""


class FakeClient:
    def __init__(self, body=b"%PDF-1.7\nhello", html=HTML):
        self.body = body
        self.html = html
        self.calls = 0

    def get_html(self, url):
        return self.html

    def download(self, resource, temp_dir):
        self.calls += 1
        if isinstance(self.body, Exception):
            raise self.body
        with tempfile.NamedTemporaryFile(
            dir=temp_dir, suffix=".part", delete=False
        ) as stream:
            stream.write(self.body)
            path = Path(stream.name)
        return Download(
            path,
            "Introduction.pdf",
            hashlib.sha256(self.body).hexdigest(),
            len(self.body),
            "https://egeders.ege.edu.tr/pluginfile.php/1/test.pdf",
            "application/pdf",
        )


def run(client, manifest, root, cancel=None):
    return sync_courses(
        client, manifest, Account("teststudent"), [COURSE], root, lambda _: None, cancel
    )


def test_repeat_verifies_bytes_and_does_not_duplicate(tmp_path):
    client = FakeClient()
    with Manifest(tmp_path / "manifest.db") as manifest:
        first = run(client, manifest, tmp_path / "out")
        second = run(client, manifest, tmp_path / "out")
        assert (first.new, second.unchanged) == (1, 1)
        assert client.calls == 2
        assert len(manifest.versions(1342714)) == 1
        assert len(list((tmp_path / "out").rglob("*.pdf"))) == 1
        assert not list((tmp_path / "out").rglob("*.part"))


def test_replacement_preserves_old_bytes_and_user_edits(tmp_path):
    with Manifest(tmp_path / "manifest.db") as manifest:
        run(FakeClient(), manifest, tmp_path / "out")
        old = Path(manifest.latest(1342714)["path"])
        original = old.read_bytes()
        changed = run(FakeClient(b"%PDF-1.7\nworld"), manifest, tmp_path / "out")
        assert changed.updated == 1
        assert old.read_bytes() == original
        current = Path(manifest.latest(1342714)["path"])
        current.write_bytes(b"user-edited")
        restored = run(FakeClient(b"%PDF-1.7\nworld"), manifest, tmp_path / "out")
        assert restored.updated == 1
        assert current.read_bytes() == b"user-edited"
        assert Path(manifest.latest(1342714)["path"]).read_bytes() == b"%PDF-1.7\nworld"


def test_missing_remote_resource_never_deletes_local_file(tmp_path):
    with Manifest(tmp_path / "manifest.db") as manifest:
        run(FakeClient(), manifest, tmp_path / "out")
        old = Path(manifest.latest(1342714)["path"])
        result = run(
            FakeClient(html="<main>No files</main>"), manifest, tmp_path / "out"
        )
        assert result.failed == 0
        assert old.is_file()
        assert len(manifest.versions(1342714)) == 1


def test_failures_preserve_history_and_auth_aborts(tmp_path):
    with Manifest(tmp_path / "manifest.db") as manifest:
        run(FakeClient(), manifest, tmp_path / "out")
        old = Path(manifest.latest(1342714)["path"])
        result = run(
            FakeClient(ValueError("Truncated response")), manifest, tmp_path / "out"
        )
        assert result.failed == 1
        assert old.is_file()
        with pytest.raises(AuthExpiredError):
            run(
                FakeClient(AuthExpiredError("Login required")),
                manifest,
                tmp_path / "out",
            )


def test_cancellation_stops_before_network(tmp_path):
    client = FakeClient()
    cancel = threading.Event()
    cancel.set()
    with Manifest(tmp_path / "manifest.db") as manifest:
        result = run(client, manifest, tmp_path / "out", cancel)
    assert result.cancelled
    assert client.calls == 0


def test_changed_output_creates_copy_at_new_location(tmp_path):
    with Manifest(tmp_path / "manifest.db") as manifest:
        run(FakeClient(), manifest, tmp_path / "first")
        result = run(FakeClient(), manifest, tmp_path / "second")
        assert result.updated == 1
        assert len(list((tmp_path / "first").rglob("*.pdf"))) == 1
        assert len(list((tmp_path / "second").rglob("*.pdf"))) == 1


def test_renamed_section_reuses_verified_history(tmp_path):
    with Manifest(tmp_path / "manifest.db") as manifest:
        run(FakeClient(), manifest, tmp_path / "out")
        original = manifest.latest(1342714)["path"]
        changed = HTML.replace(">Introduction</h3>", ">Renamed section</h3>")
        result = run(FakeClient(html=changed), manifest, tmp_path / "out")
        assert result.unchanged == 1
        assert manifest.latest(1342714)["path"] == original
        assert manifest.latest(1342714)["section"] == "Renamed section"
        assert len(list((tmp_path / "out").rglob("*.pdf"))) == 1


def test_manifest_failure_after_publish_recovers_without_duplicate(tmp_path, monkeypatch):
    output = tmp_path / "out"
    with Manifest(tmp_path / "manifest.db") as manifest:
        record = manifest.record

        def failing_record(*_args):
            raise OSError("Simulated database write failure after file publication")

        monkeypatch.setattr(manifest, "record", failing_record)
        failed = run(FakeClient(), manifest, output)
        assert failed.failed == 1
        assert manifest.versions(1342714) == []
        orphan_files = list(output.rglob("*.pdf"))
        assert len(orphan_files) == 1
        orphan = orphan_files[0]
        original_bytes = orphan.read_bytes()

        monkeypatch.setattr(manifest, "record", record)
        recovered = run(FakeClient(), manifest, output)
        assert recovered.failed == 0
        assert recovered.unchanged == 1
        assert list(output.rglob("*.pdf")) == [orphan]
        assert orphan.read_bytes() == original_bytes
        assert len(manifest.versions(1342714)) == 1
        assert manifest.latest(1342714)["path"] == str(orphan.resolve())
        assert not list(output.rglob("*.part"))
