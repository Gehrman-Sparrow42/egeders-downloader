import hashlib
import tempfile
import threading
from pathlib import Path

import pytest

from egeders_downloader.manifest import Manifest
from egeders_downloader.models import (
    Account,
    AuthExpiredError,
    ConverterInfo,
    Course,
    Download,
)
from egeders_downloader.paths import choose_destination, resource_directory
from egeders_downloader.sync_engine import sync_courses

COURSE = Course(
    178423,
    "Engineering Economics 2627G",
    "https://egeders.ege.edu.tr/course/view.php?id=178423",
)
HTML = """<li class="section"><h3 class="sectionname">Introduction</h3>
<li class="activity"><a href="/mod/resource/view.php?id=1342714">Introduction</a></li></li>"""
PPTX_HTML = """<li class="section"><h3 class="sectionname">Week 1</h3>
<li class="activity"><a href="/mod/resource/view.php?id=1342714">Lecture 1.pptx</a></li>
<li class="activity"><a href="/mod/quiz/view.php?id=800">Midterm Quiz</a></li>
<li class="activity"><a href="/mod/forum/view.php?id=801">Course Forum</a></li></li>"""


class FakeClient:
    def __init__(
        self,
        body=b"%PDF-1.7\nhello",
        html=HTML,
        filename="Introduction.pdf",
        content_type="application/pdf",
    ):
        self.body = body
        self.html = html
        self.filename = filename
        self.content_type = content_type
        self.calls = 0
        self.downloaded_urls: list[str] = []

    def get_html(self, url):
        return self.html

    def download(self, resource, temp_dir):
        self.calls += 1
        self.downloaded_urls.append(resource.url)
        if isinstance(self.body, Exception):
            raise self.body
        with tempfile.NamedTemporaryFile(
            dir=temp_dir, suffix=".part", delete=False
        ) as stream:
            stream.write(self.body)
            path = Path(stream.name)
        return Download(
            path,
            self.filename,
            hashlib.sha256(self.body).hexdigest(),
            len(self.body),
            f"https://egeders.ege.edu.tr/pluginfile.php/1/{self.filename}",
            self.content_type,
        )


def run(client, manifest, root, cancel=None, convert_pptx=False, converter=None, log=lambda _: None):
    return sync_courses(
        client,
        manifest,
        Account("teststudent"),
        [COURSE],
        root,
        log,
        cancel,
        convert_pptx=convert_pptx,
        converter=converter,
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


def test_sync_logs_unsupported_activity_names_and_never_follows_them(tmp_path):
    logs: list[str] = []
    client = FakeClient(
        body=b"PK\x03\x04slide-bytes",
        html=PPTX_HTML,
        filename="Lecture 1.pptx",
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    with Manifest(tmp_path / "manifest.db") as manifest:
        summary = run(client, manifest, tmp_path / "out", log=logs.append)
    assert summary.new == 1
    assert summary.unsupported == 2
    assert client.downloaded_urls == ["https://egeders.ege.edu.tr/mod/resource/view.php?id=1342714"]
    joined = "\n".join(logs)
    assert "Midterm Quiz" in joined
    assert "Course Forum" in joined


def test_pptx_conversion_new_unchanged_idempotent_and_retry(tmp_path):
    slide_bytes = b"PK\x03\x04synthetic-pptx-payload"
    pdf_bytes = b"%PDF-1.7\nconverted slide content\n%%EOF\n"
    client = FakeClient(
        body=slide_bytes,
        html=PPTX_HTML,
        filename="Lecture 1.pptx",
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    conversions = []

    def working_converter(source: Path, target: Path):
        conversions.append(source)
        target.write_bytes(pdf_bytes)

    output = tmp_path / "out"
    with Manifest(tmp_path / "manifest.db") as manifest:
        # 1. Converter unavailable: download still succeeds and is not marked failed.
        unavailable = ConverterInfo(False, "none", "No converter installed")
        first = run(client, manifest, output, convert_pptx=True, converter=unavailable)
        assert (first.new, first.failed, first.converted, first.conversion_failed) == (1, 0, 0, 1)
        pptx_files = list(output.rglob("*.pptx"))
        assert len(pptx_files) == 1
        assert pptx_files[0].read_bytes() == slide_bytes
        assert list(output.rglob("*.pdf")) == []

        # 2. Converter fails mid-conversion on unchanged presentation: download stays unchanged, not failed.
        def failing_converter(_source: Path, _target: Path):
            raise RuntimeError("Simulated COM error")

        second = run(client, manifest, output, convert_pptx=True, converter=failing_converter)
        assert (second.unchanged, second.failed, second.converted, second.conversion_failed) == (1, 0, 0, 1)
        assert pptx_files[0].read_bytes() == slide_bytes
        assert list(output.rglob("*.pdf")) == []
        assert not list(output.rglob("*.part"))

        # 3. Retry on next sync with working converter succeeds on unchanged presentation.
        third = run(client, manifest, output, convert_pptx=True, converter=working_converter)
        assert (third.unchanged, third.failed, third.converted, third.conversion_failed) == (1, 0, 1, 0)
        assert len(conversions) == 1
        pdf_files = list(output.rglob("*.pdf"))
        assert len(pdf_files) == 1
        assert pdf_files[0].read_bytes() == pdf_bytes
        assert pptx_files[0].read_bytes() == slide_bytes

        # 4. Repeat sync is idempotent: does not re-run converter or create duplicate PDFs.
        fourth = run(client, manifest, output, convert_pptx=True, converter=working_converter)
        assert (fourth.unchanged, fourth.failed, fourth.converted, fourth.conversion_failed) == (1, 0, 0, 0)
        assert len(conversions) == 1
        assert list(output.rglob("*.pdf")) == pdf_files


def test_conversion_preserves_existing_pdf_and_user_edits(tmp_path):
    slide_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy-ppt"
    pdf_bytes = b"%PDF-1.7\nfreshly converted\n%%EOF\n"
    client = FakeClient(
        body=slide_bytes,
        html=PPTX_HTML,
        filename="Lecture 1.ppt",
        content_type="application/vnd.ms-powerpoint",
    )
    output = tmp_path / "out"
    account = Account("teststudent")
    from egeders_downloader.parser import parse_course

    resource = parse_course(PPTX_HTML, COURSE.id).resources[0]
    directory = resource_directory(output, account, COURSE, resource)
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(slide_bytes).hexdigest()
    preexisting_pdf = choose_destination(directory, "Lecture 1.pdf", resource.id, digest)
    preexisting_pdf.write_bytes(b"%PDF-1.7\nuser preexisting pdf\n%%EOF\n")

    def converter(_source: Path, target: Path):
        target.write_bytes(pdf_bytes)

    with Manifest(tmp_path / "manifest.db") as manifest:
        first = run(client, manifest, output, convert_pptx=True, converter=converter)
        assert (first.new, first.converted, first.failed) == (1, 1, 0)
        assert preexisting_pdf.read_bytes() == b"%PDF-1.7\nuser preexisting pdf\n%%EOF\n"
        latest_source = Path(manifest.latest(resource.id)["path"])
        assert latest_source.read_bytes() == slide_bytes
        conv = manifest.conversion(resource.id, digest, latest_source)
        converted_pdf = Path(conv["pdf_path"])
        assert converted_pdf != preexisting_pdf
        assert converted_pdf.read_bytes() == pdf_bytes

        # User annotates/edits the converted PDF; next sync must preserve user edits and create a clean copy.
        converted_pdf.write_bytes(b"%PDF-1.7\nuser annotated converted pdf\n%%EOF\n")
        second = run(client, manifest, output, convert_pptx=True, converter=converter)
        assert (second.unchanged, second.converted, second.failed) == (1, 1, 0)
        assert preexisting_pdf.read_bytes() == b"%PDF-1.7\nuser preexisting pdf\n%%EOF\n"
        assert converted_pdf.read_bytes() == b"%PDF-1.7\nuser annotated converted pdf\n%%EOF\n"
        updated_conv = manifest.conversion(resource.id, digest, latest_source)
        replacement_pdf = Path(updated_conv["pdf_path"])
        assert replacement_pdf not in {preexisting_pdf, converted_pdf}
        assert replacement_pdf.read_bytes() == pdf_bytes
