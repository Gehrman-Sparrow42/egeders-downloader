"""Network boundary and byte-integrity tests without real credentials."""

from types import SimpleNamespace

import pytest

from egeders_downloader.browser_client import (
    BrowserClient,
    response_filename,
    validate_moodle_url,
    validate_payload,
)
from egeders_downloader.models import (
    Account,
    AuthExpiredError,
    InvalidContentError,
    Resource,
)


@pytest.mark.parametrize("url", [
    "http://egeders.ege.edu.tr/pluginfile.php/a.pdf",
    "https://evil.test/pluginfile.php/a.pdf",
    "https://egeders.ege.edu.tr.evil.test/pluginfile.php/a.pdf",
    "https://user:secret@egeders.ege.edu.tr/pluginfile.php/a.pdf",
    "https://egeders.ege.edu.tr:8443/pluginfile.php/a.pdf",
    "https://egeders.ege.edu.tr/mod/quiz/view.php?id=1",
    "https://egeders.ege.edu.tr/mod/resource/view.php?id=1&redirect=1",
])
def test_unsafe_download_url_rejected(url):
    with pytest.raises(InvalidContentError):
        validate_moodle_url(url, file=True)


def test_pdf_magic_and_truncation():
    validate_payload(b"%PDF-1.7\n", {"content-length": "9"}, "sample.pdf")
    with pytest.raises(InvalidContentError, match="PDF header"):
        validate_payload(b"not a pdf", {}, "sample.pdf")
    with pytest.raises(InvalidContentError, match="Incomplete"):
        validate_payload(b"%PDF-1.7\n", {"content-length": "20"}, "sample.pdf")
    with pytest.raises(InvalidContentError, match="HTML"):
        validate_payload(b"<!DOCTYPE html><html>Sign in</html>", {}, "sample.pdf")


def test_download_filename_removes_paths_and_decodes_unicode():
    assert response_filename({"content-disposition": "attachment; filename*=UTF-8''Ders%20notlar%C4%B1.pdf"}, "https://example.test/a") == "Ders notları.pdf"
    assert response_filename({"content-disposition": 'attachment; filename="../../lecture.pdf"'}, "https://example.test/a") == "lecture.pdf"


class Response:
    def __init__(self, url, status=200, headers=None, body=b"%PDF-1.7\n"):
        self.url, self.status, self.headers, self.payload = url, status, headers or {}, body
        self.disposed = False

    def body(self):
        return self.payload

    def dispose(self):
        self.disposed = True


def client_with_responses(tmp_path, monkeypatch, responses):
    monkeypatch.setattr("egeders_downloader.browser_client.time.sleep", lambda _: None)
    client = BrowserClient(Account("student"), tmp_path)
    visited = []

    def get(url, **kwargs):
        assert kwargs["max_redirects"] == 0
        visited.append(url)
        return responses.pop(0)

    client.context = SimpleNamespace(request=SimpleNamespace(get=get))
    return client, visited


def test_third_party_redirect_never_requested(tmp_path, monkeypatch):
    url = "https://egeders.ege.edu.tr/mod/resource/view.php?id=1"
    response = Response(url, 302, {"location": "https://evil.test/file.pdf"})
    client, visited = client_with_responses(tmp_path, monkeypatch, [response])
    with pytest.raises(InvalidContentError):
        client._request(url, file=True)
    assert visited == [url]
    assert response.disposed


def test_sso_redirect_requires_login_before_request(tmp_path, monkeypatch):
    url = "https://egeders.ege.edu.tr/mod/resource/view.php?id=1"
    client, visited = client_with_responses(tmp_path, monkeypatch, [Response(url, 302, {"location": "https://kimlik.ege.edu.tr/Identity/Account/Login"})])
    with pytest.raises(AuthExpiredError):
        client._request(url, file=True)
    assert visited == [url]


def test_successful_pdf_download_hashes_and_disposes(tmp_path, monkeypatch):
    url = "https://egeders.ege.edu.tr/mod/resource/view.php?id=1"
    final = "https://egeders.ege.edu.tr/pluginfile.php/123/file.pdf"
    response = Response(final, headers={"content-type": "application/pdf", "content-length": "9"})
    client, visited = client_with_responses(tmp_path, monkeypatch, [Response(url, 303, {"location": final}), response])
    result = client.download(Resource(1, 1, "File", "Week 1", url), tmp_path / "downloads")
    assert result.temp_path.read_bytes() == b"%PDF-1.7\n"
    assert result.size == 9
    assert len(result.sha256) == 64
    assert result.filename == "file.pdf"
    assert visited == [url, final]
    assert response.disposed


def test_truncated_download_leaves_no_partial_file(tmp_path, monkeypatch):
    url = "https://egeders.ege.edu.tr/pluginfile.php/1/file.pdf"
    response = Response(url, headers={"content-type": "application/pdf", "content-length": "100"})
    client, _ = client_with_responses(tmp_path, monkeypatch, [response])
    with pytest.raises(InvalidContentError):
        client.download(Resource(1, 1, "File", "", url), tmp_path / "downloads")
    assert not list(tmp_path.rglob("*.part"))
    assert response.disposed


def test_other_account_session_never_reused(tmp_path):
    client = BrowserClient(Account("student"), tmp_path)
    client.identity_path.parent.mkdir(parents=True)
    client.identity_path.write_text('{"account":"someoneelse@ogrenci.ege.edu.tr"}')
    assert client.check_session() is False


def test_login_survives_navigation_race_and_closed_source_tab(tmp_path, monkeypatch):
    from playwright.sync_api import Error as PlaywrightError

    monkeypatch.setattr("egeders_downloader.browser_client.time.sleep", lambda _: None)
    client = BrowserClient(Account("student"), tmp_path)
    saved = []
    monkeypatch.setattr(client, "check_session", lambda: False)
    monkeypatch.setattr(client, "_save_session", lambda: saved.append(True))

    class Locator:
        first = property(lambda self: self)

        def __init__(self, selector):
            self.selector = selector

        def fill(self, _value):
            pass

        def select_option(self, *_args, **_kwargs):
            pass

        def count(self):
            return 1

        def click(self):
            if self.selector.startswith("a["):
                source.closed = True
                context.pages.append(destination)

    class Page:
        def __init__(self, url, html, race=False):
            self.url, self.html, self.race, self.closed = url, html, race, False

        def goto(self, *_args, **_kwargs):
            pass

        def locator(self, selector, **_kwargs):
            return Locator(selector)

        def is_closed(self):
            return self.closed

        def content(self):
            if self.race:
                self.race = False
                raise PlaywrightError("Page.content: Unable to retrieve content because the page is navigating and changing the content.")
            return self.html

    source = Page("https://kimlik.ege.edu.tr/", "<html><body>Account dashboard</body></html>", race=True)
    destination = Page("https://egeders.ege.edu.tr/", '<html><body><div id="user-menu-toggle">Student</div></body></html>')
    context = SimpleNamespace(pages=[source], close=lambda: None, new_page=lambda: source)
    client.context = context
    client.browser = SimpleNamespace(new_context=lambda **_kwargs: context)
    client.login(password="test password", timeout_seconds=2)
    assert saved == [True]
    assert source.closed


def test_login_translates_nontransient_playwright_error(tmp_path, monkeypatch):
    from playwright.sync_api import Error as PlaywrightError

    client = BrowserClient(Account("student"), tmp_path)

    def fail(*_args):
        raise PlaywrightError("sensitive browser diagnostics")

    monkeypatch.setattr(client, "_login", fail)
    with pytest.raises(AuthExpiredError, match="Browser sign-in could not finish"):
        client.login(password="test password")
