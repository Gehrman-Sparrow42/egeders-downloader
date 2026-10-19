"""Account-isolated browser login and bounded, same-origin material downloads.

Playwright's request API buffers response bodies. The 150 MiB limit rejects an
advertised large response before reading, and checks actual bytes afterwards;
it is not a streaming memory limit for servers without Content-Length.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .models import (
    MOODLE_ORIGIN,
    SSO_URL,
    Account,
    AuthExpiredError,
    Download,
    InvalidContentError,
    Resource,
)

MAX_BYTES = 150 * 1024 * 1024


def validate_moodle_url(url: str, *, file: bool = False) -> str:
    """Restrict network reads to the supported Moodle endpoints, including hops."""
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "egeders.ege.edu.tr"
            or parsed.port not in (None, 443) or parsed.username or parsed.password):
        raise InvalidContentError("Resource URL must stay on the official HTTPS Ege Ders host.")
    if parsed.path.startswith("/login/"):
        raise AuthExpiredError("Ege Ders session expired. Sign in again.")
    if file and (parsed.path.startswith("/pluginfile.php/") or parsed.path == "/pluginfile.php"):
        return url
    permitted = {"/mod/resource/view.php"} if file else {"/", "/my/", "/my/index.php", "/index.php", "/course/view.php"}
    if parsed.path not in permitted:
        raise InvalidContentError("Unsupported Moodle URL; only course pages and file resources are read.")
    if parsed.path.endswith("view.php"):
        values = parse_qs(parsed.query)
        if (set(values) != {"id"} or len(values["id"]) != 1
                or not values["id"][0].isdigit() or int(values["id"][0]) <= 0):
            raise InvalidContentError("Moodle page URL must have exactly one numeric id.")
    elif parsed.query and not file:
        raise InvalidContentError("Unexpected dashboard URL parameters.")
    return url


def response_filename(headers: dict[str, str], url: str) -> str:
    message = Message()
    message["content-disposition"] = headers.get("content-disposition", "")
    filename = message.get_filename() or unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    # Storage applies Windows sanitization, but never pass a directory from HTTP.
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return filename if filename not in {"", ".", ".."} else "material.bin"


def validate_payload(body: bytes, headers: dict[str, str], filename: str) -> None:
    if not body or len(body) > MAX_BYTES:
        raise InvalidContentError("File is empty or exceeds the 150 MiB download limit.")
    content_type = headers.get("content-type", "").lower()
    prefix = body[:1024].lstrip().lower()
    if "text/html" in content_type or prefix.startswith((b"<!doctype html", b"<html")):
        raise InvalidContentError("Server returned an HTML page instead of a downloadable file.")
    if (filename.lower().endswith(".pdf") or "application/pdf" in content_type) and not body.startswith(b"%PDF-"):
        raise InvalidContentError("PDF resource does not contain a PDF header.")
    length = headers.get("content-length")
    if length and not headers.get("content-encoding"):
        try:
            expected = int(length)
        except ValueError as exc:
            raise InvalidContentError("Server returned an invalid file length.") from exc
        if expected != len(body):
            raise InvalidContentError("Incomplete file: received size differs from Content-Length.")


def _is_navigation_race(error: Exception) -> bool:
    text = str(error).lower()
    return any(fragment in text for fragment in (
        "page is navigating", "execution context was destroyed",
        "frame was detached", "frame has been detached",
        "target page, context or browser has been closed", "target closed",
        "element is not attached to the dom",
    ))


class BrowserClient:
    def __init__(self, account: Account, state_dir: Path, headless: bool = True,
                 log: Callable[[str], None] = print):
        self.account = account
        self.headless = headless
        self.log = log
        self.session_path = Path(state_dir) / "accounts" / account.slug / "storage_state.json"
        self.identity_path = self.session_path.with_name("session_account.json")
        self._playwright = None
        self.browser = None
        self.context = None
        self._last_request = 0.0

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        try:
            try:
                self.browser = self._playwright.chromium.launch(channel="msedge", headless=self.headless)
            except Exception:  # noqa: BLE001 - browser availability fallback, no auth attempted
                if not Path(self._playwright.chromium.executable_path).is_file():
                    raise RuntimeError("Install Microsoft Edge or run: python -m playwright install chromium") from None
                self.browser = self._playwright.chromium.launch(headless=self.headless)
            state = str(self.session_path) if self.session_path.is_file() else None
            try:
                self.context = self.browser.new_context(storage_state=state, accept_downloads=True)
            except Exception:
                if state is None:
                    raise
                self.log("Saved browser session could not be loaded; a new sign-in is needed.")
                self.context = self.browser.new_context(accept_downloads=True)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        try:
            if self.context:
                self.context.close()
        finally:
            try:
                if self.browser:
                    self.browser.close()
            finally:
                if self._playwright:
                    self._playwright.stop()
        self.context = self.browser = self._playwright = None

    def _request(self, url: str, *, file: bool = False):
        """GET with explicit same-origin redirects and two transient retries."""
        if self.context is None:
            raise RuntimeError("Use BrowserClient inside a with statement.")
        for _hop in range(8):
            validate_moodle_url(url, file=file)
            for attempt in range(3):
                time.sleep(max(0, 0.5 - (time.monotonic() - self._last_request)))
                self._last_request = time.monotonic()
                try:
                    response = self.context.request.get(url, max_redirects=0, timeout=30000)
                except Exception:  # noqa: BLE001 - translate transport failures without exposing URL secrets
                    if attempt == 2:
                        raise RuntimeError("Ege Ders request failed after three attempts.") from None
                    time.sleep(attempt + 1)
                    continue
                if response.status == 429 or 500 <= response.status <= 599:
                    response.dispose()
                    if attempt == 2:
                        raise RuntimeError("Ege Ders is temporarily unavailable; retry later.")
                    time.sleep(attempt + 1)
                    continue
                break
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                response.dispose()
                if not location:
                    raise InvalidContentError("Server returned an empty redirect.")
                target = urljoin(url, location)
                if urlsplit(target).hostname == "kimlik.ege.edu.tr" or "/login/" in urlsplit(target).path:
                    raise AuthExpiredError("Ege Ders session expired. Sign in again.")
                validate_moodle_url(target, file=file)
                url = target
                continue
            if response.status in (401, 403):
                response.dispose()
                raise AuthExpiredError("Session expired or access to this course was denied.")
            if response.status != 200:
                response.dispose()
                raise InvalidContentError(f"Ege Ders returned HTTP {response.status}.")
            return response
        raise InvalidContentError("Too many redirects resolving the Moodle resource.")

    def get_html(self, url: str) -> str:
        from .parser import is_authenticated
        response = self._request(url)
        try:
            html = response.text()
            if not is_authenticated(html):
                raise AuthExpiredError("Ege Ders session expired. Sign in again.")
            return html
        finally:
            response.dispose()

    def check_session(self) -> bool:
        try:
            # Only reuse state saved after verifying this account's login.
            identity = json.loads(self.identity_path.read_text(encoding="utf-8"))
            if identity.get("account") != self.account.key:
                return False
            self.get_html(MOODLE_ORIGIN + "/")
            return True
        except (AuthExpiredError, OSError, ValueError):
            return False

    def _save_session(self):
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        for target, data in ((self.session_path, self.context.storage_state()),
                             (self.identity_path, {"account": self.account.key})):
            fd, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)

    def login(self, password: str | None = None, manual: bool = False, timeout_seconds: int = 180) -> None:
        from playwright.sync_api import Error as PlaywrightError
        try:
            self._login(password, manual, timeout_seconds)
        except PlaywrightError:
            raise AuthExpiredError("Browser sign-in could not finish. Retry sign-in; keep the browser open until it completes.") from None

    def _login(self, password: str | None, manual: bool, timeout_seconds: int) -> None:
        from playwright.sync_api import Error as PlaywrightError
        if self.context is None:
            raise RuntimeError("Use BrowserClient inside a with statement.")
        if self.check_session():
            self.log("Saved Ege Ders session is active.")
            return
        if manual and self.headless:
            raise ValueError("Manual sign-in requires a visible browser (headless=False).")
        if not manual and not password:
            raise AuthExpiredError("Sign in manually or provide a password to create a session.")
        # Start a fresh context so another account's SSO cookies cannot bypass login.
        self.context.close()
        self.context = self.browser.new_context(accept_downloads=True)
        page = self.context.new_page()
        page.goto(SSO_URL, wait_until="domcontentloaded", timeout=30000)
        if not manual:
            page.locator("#username").fill(self.account.username)
            page.locator("#Input_Domain").select_option(self.account.domain, force=True)
            page.locator("#password").fill(password)
            page.locator("#login-submit").click()
        else:
            self.log(f"Complete browser sign-in as {self.account.key}; the tool will open Ege Ders.")
        deadline = time.monotonic() + timeout_seconds
        clicked = False
        verified_identity = not manual
        while time.monotonic() < deadline:
            for candidate in list(self.context.pages):
                try:
                    if candidate.is_closed():
                        continue
                    if urlsplit(candidate.url).hostname == "egeders.ege.edu.tr":
                        from .parser import is_authenticated
                        if is_authenticated(candidate.content()):
                            if not verified_identity:
                                raise AuthExpiredError("Could not verify the signed-in account; use automatic sign-in for the selected username.")
                            self._save_session()
                            self.log("Signed in to Ege Ders; session saved locally.")
                            return
                    if urlsplit(candidate.url).hostname != "kimlik.ege.edu.tr":
                        continue
                    html = candidate.content()
                    if not manual:
                        soup = BeautifulSoup(html, "html.parser")
                        errors = soup.select(".validation-summary-errors, .field-validation-error, .alert-danger")
                        if any(error.get_text(strip=True) for error in errors):
                            raise AuthExpiredError("University sign-in was rejected. Check the username, domain, and password.")
                    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()
                    if self.account.key in text:
                        # A full account key is needed to distinguish university domains.
                        verified_identity = True
                    link = candidate.locator('a[href*="/Saml/MoodleSingleSignOn"]', has_text=re.compile("Ege\\s*Ders", re.IGNORECASE))
                    if not clicked and link.count():
                        link.first.click()
                        clicked = True
                except PlaywrightError as exc:
                    if not _is_navigation_race(exc):
                        raise
                    continue
            # A SAML source tab can close itself; never wait on the original page.
            time.sleep(0.25)
        raise AuthExpiredError("Sign-in timed out. Check credentials or finish the browser sign-in and retry.")

    def download(self, resource: Resource, temp_dir: Path) -> Download:
        url = validate_moodle_url(resource.url, file=True)
        for _step in range(3):
            response = self._request(url, file=True)
            try:
                headers = response.headers
                length = headers.get("content-length", "")
                if length.isdigit() and int(length) > MAX_BYTES:
                    raise InvalidContentError("File exceeds the 150 MiB download limit.")
                body = response.body()
                final_url = response.url
                content_type = headers.get("content-type", "")
                if "text/html" in content_type.lower() or body[:1024].lstrip().lower().startswith((b"<!doctype html", b"<html")):
                    from .parser import is_authenticated
                    html = body.decode("utf-8", errors="replace")
                    if not is_authenticated(html):
                        raise AuthExpiredError("Ege Ders returned a sign-in page. Sign in again.")
                    soup = BeautifulSoup(html, "html.parser")
                    targets = [node.get(attr) for selector, attr in (("object[data]", "data"), ("iframe[src]", "src"), ("a[href]", "href")) for node in soup.select(selector)]
                    candidates = [urljoin(final_url, target) for target in targets if target and "/pluginfile.php/" in target]
                    if not candidates:
                        raise InvalidContentError("Resource page did not contain a supported file link.")
                    url = validate_moodle_url(candidates[0], file=True)
                    continue
                if not urlsplit(final_url).path.startswith("/pluginfile.php/"):
                    raise InvalidContentError("Resource did not resolve to a Moodle pluginfile URL.")
                filename = response_filename(headers, final_url)
                validate_payload(body, headers, filename)
            finally:
                response.dispose()
            temp_dir.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix="download-", suffix=".part", dir=temp_dir)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(body)
                    handle.flush()
                    os.fsync(handle.fileno())
                return Download(temporary, filename, hashlib.sha256(body).hexdigest(), len(body), final_url, content_type)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        raise InvalidContentError("Resource page repeatedly linked to another HTML page.")

