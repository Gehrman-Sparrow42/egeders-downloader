from concurrent.futures import ThreadPoolExecutor

from egeders_downloader.manifest import Manifest
from egeders_downloader.models import Course, Download, Resource


def test_history_rechecks_and_no_deletion(tmp_path):
    course = Course(10, "Course", "https://egeders.ege.edu.tr/course/view.php?id=10")
    resource = Resource(10, 22, "Lecture", "Week 1", "https://egeders.ege.edu.tr/mod/resource/view.php?id=22", "573 KB")
    path1, path2 = tmp_path / "one.pdf", tmp_path / "two.pdf"
    first = Download(tmp_path / "temp", "lecture.pdf", "a" * 64, 42, "https://egeders.ege.edu.tr/pluginfile.php/a", "application/pdf")
    second = Download(tmp_path / "temp", "lecture.pdf", "b" * 64, 80, first.url, first.content_type)
    database = tmp_path / "account" / "manifest.sqlite3"
    with Manifest(database) as manifest:
        manifest.record(course, resource, first, path1)
        manifest.record(course, resource, first, path1)
        assert len(manifest.versions(22)) == 1
        manifest.record(course, resource, second, path2)
        assert len(manifest.versions(22)) == 2
        assert manifest.latest(22)["sha256"] == second.sha256
        assert manifest.latest(22)["size"] == 80
        assert manifest.latest(22)["path"] == str(path2.resolve())
        assert manifest.latest(999) is None
    # Reopening without crawling old resources preserves every version.
    manifest = Manifest(database)
    assert len(manifest.versions(22)) == 2
    assert manifest.status()[0]["course_title"] == "Course"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(manifest.latest, [22, 22]))
    assert all(result["sha256"] == second.sha256 for result in results)
