"""Local persistence, inventory enrichment and process-lock behavior without opening a browser."""

import pytest

from egeders_downloader.manifest import Manifest
from egeders_downloader.models import Account, Course, Download, Resource
from egeders_downloader.service import Settings, account_lock, inspect_courses


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


def test_inspect_courses_never_downloads_and_enriches_from_manifest(tmp_path):
    course = Course(10, "Algorithms 2627G", "https://egeders.ege.edu.tr/course/view.php?id=10")
    html = """<li class="section"><h3 class="sectionname">Week 1</h3>
      <li class="activity"><a href="/mod/resource/view.php?id=50">Lecture Slides</a></li>
      <li class="activity"><a href="/mod/assign/view.php?id=51">Project 1</a></li>
      <li class="activity"><a href="/mod/url/view.php?id=52">External Docs</a></li>
    </li>"""
    pptx_file = tmp_path / "slides.pptx"
    pptx_file.write_bytes(b"PK\x03\x04")
    pdf_file = tmp_path / "slides.pdf"
    pdf_file.write_bytes(b"%PDF-1.7\n%%EOF\n")

    class ReadOnlyClient:
        def __init__(self):
            self.visited = []

        def get_html(self, url):
            self.visited.append(url)
            return html

        def download(self, *_args, **_kwargs):
            pytest.fail("Inventory browsing must never download resources")

    client = ReadOnlyClient()
    with Manifest(tmp_path / "manifest.sqlite3") as manifest:
        resource = Resource(10, 50, "Lecture Slides", "Week 1", "https://egeders.ege.edu.tr/mod/resource/view.php?id=50")
        download = Download(
            pptx_file,
            "Week1_Slides.pptx",
            "a" * 64,
            4,
            "https://egeders.ege.edu.tr/pluginfile.php/1/Week1_Slides.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        manifest.record(course, resource, download, pptx_file)
        manifest.record_conversion(
            50,
            "a" * 64,
            pptx_file,
            status="converted",
            converter="powerpoint",
            pdf_path=pdf_file,
            pdf_sha256="b" * 64,
            pdf_size=15,
        )
        items = inspect_courses(client, manifest, [course], log=lambda _: None)

    assert client.visited == [course.url]
    assert len(items) == 3
    assert items[0].type_label == "File (PPTX)"
    assert "Downloaded (Week1_Slides.pptx) + PDF (slides.pdf)" == items[0].status
    assert items[1].type_label == "Assignment"
    assert not items[1].downloadable
    assert items[2].type_label == "URL"
    assert not items[2].downloadable
