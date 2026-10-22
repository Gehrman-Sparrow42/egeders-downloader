"""Application entry points shared by GUI and CLI."""

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from .models import Account, Activity, AuthExpiredError, ConverterInfo, Course

DEFAULT_STATE = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "EgeDersDownloader"
DEFAULT_OUTPUT = Path.home() / "Documents" / "Ege Ders Materials"


class Settings:
    def __init__(self, state_dir: Path | None = None):
        self.state_dir = Path(state_dir or DEFAULT_STATE).expanduser().resolve()

    def load(self) -> dict:
        path = self.state_dir / "settings.json"
        if not path.exists():
            return {}
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ValueError(f"Cannot read settings: {path}. {exc}") from exc
        if not isinstance(result, dict):
            raise ValueError("Settings must contain a JSON object.")  # noqa: TRY004 -- invalid file content
        return result

    def save(self, data: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            dir=self.state_dir, prefix="settings-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.state_dir / "settings.json")
        finally:
            Path(name).unlink(missing_ok=True)


@contextmanager
def account_lock(state_dir: Path, account: Account):
    """OS-backed lock released on process exit, including crashes."""
    directory = Path(state_dir) / "accounts" / account.slug
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "operation.lock"
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                "Another operation is using this account. Wait for it to finish."
            ) from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_UN)


def converter_status() -> ConverterInfo:
    from .converter import detect_converter

    return detect_converter()


def login(
    account: Account,
    state_dir: Path,
    password: str | None = None,
    manual: bool = False,
    log=print,
) -> None:
    from .browser_client import BrowserClient

    with account_lock(state_dir, account):  # noqa: SIM117 -- lock precedes browser startup
        with BrowserClient(
            account, Path(state_dir), headless=not manual, log=log
        ) as client:
            client.login(password=password, manual=manual)


def discover(account: Account, state_dir: Path, log=print):
    from .browser_client import BrowserClient
    from .parser import parse_courses

    with account_lock(state_dir, account):  # noqa: SIM117 -- lock precedes browser startup
        with BrowserClient(account, Path(state_dir), log=log) as client:
            if not client.check_session():
                raise AuthExpiredError("Session expired or missing. Use Login first.")
            return parse_courses(client.get_html("https://egeders.ege.edu.tr/"))


def inspect_courses(
    client,
    manifest,
    courses: list[Course],
    log=print,
    cancel: threading.Event | None = None,
) -> list[Activity]:
    """Build a read-only course inventory without downloading or following activities."""
    from .parser import infer_resource_type, parse_course

    items: list[Activity] = []
    for course in courses:
        if cancel and cancel.is_set():
            break
        log(f"Course: {course.title}")
        page = parse_course(client.get_html(course.url), course.id)
        for item in page.activities:
            type_label = item.type_label
            status_text = item.status
            if item.downloadable and manifest is not None:
                latest = manifest.latest(item.id)
                if latest is not None:
                    local_file = Path(latest["path"])
                    if type_label == "File":
                        type_label = infer_resource_type(
                            latest["filename"], item.metadata, latest["content_type"]
                        )
                    if local_file.is_file():
                        conv = manifest.conversion(item.id, latest["sha256"], local_file)
                        if (
                            conv
                            and conv["status"] == "converted"
                            and conv["pdf_path"]
                            and Path(conv["pdf_path"]).is_file()
                        ):
                            status_text = (
                                f"Downloaded ({latest['filename']}) + PDF ({Path(conv['pdf_path']).name})"
                            )
                        elif conv and conv["status"] == "failed":
                            status_text = f"Downloaded ({latest['filename']}); PDF conversion failed"
                        else:
                            status_text = f"Downloaded ({latest['filename']})"
            items.append(
                replace(
                    item,
                    type_label=type_label,
                    status=status_text,
                    course_title=course.title,
                )
            )
    return items


def inventory(
    account: Account,
    state_dir: Path,
    course_ids: list[int],
    log=print,
    cancel: threading.Event | None = None,
) -> list[Activity]:
    from .browser_client import BrowserClient
    from .manifest import Manifest
    from .parser import parse_courses

    if not course_ids:
        raise ValueError("Select at least one course before browsing inventory.")
    with account_lock(state_dir, account):  # noqa: SIM117 -- lock precedes browser startup
        with BrowserClient(account, Path(state_dir), log=log) as client:
            if not client.check_session():
                raise AuthExpiredError("Session expired or missing. Use Login first.")
            courses = parse_courses(client.get_html("https://egeders.ege.edu.tr/"))
            requested = set(course_ids)
            selected = [course for course in courses if course.id in requested]
            missing = requested - {course.id for course in selected}
            if missing:
                raise ValueError(
                    "Selected courses were not found in your course list: "
                    + ", ".join(map(str, sorted(missing)))
                )
            database = Path(state_dir) / "accounts" / account.slug / "manifest.sqlite3"
            if database.exists():
                with Manifest(database) as manifest:
                    return inspect_courses(client, manifest, selected, log, cancel)
            return inspect_courses(client, None, selected, log, cancel)


def sync(
    account: Account,
    state_dir: Path,
    output: Path,
    course_ids: list[int],
    log=print,
    cancel: threading.Event | None = None,
    convert_pptx: bool = False,
):
    from .browser_client import BrowserClient
    from .manifest import Manifest
    from .parser import parse_courses
    from .sync_engine import sync_courses

    if not course_ids:
        raise ValueError("Select at least one course before syncing.")
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    with account_lock(state_dir, account):  # noqa: SIM117 -- lock precedes browser startup
        with BrowserClient(account, Path(state_dir), log=log) as client:
            if not client.check_session():
                raise AuthExpiredError("Session expired or missing. Use Login first.")
            courses = parse_courses(client.get_html("https://egeders.ege.edu.tr/"))
            requested = set(course_ids)
            selected = [course for course in courses if course.id in requested]
            missing = requested - {course.id for course in selected}
            if missing:
                raise ValueError(
                    "Selected courses were not found in your course list: "
                    + ", ".join(map(str, sorted(missing)))
                )
            database = Path(state_dir) / "accounts" / account.slug / "manifest.sqlite3"
            with Manifest(database) as manifest:
                return sync_courses(
                    client,
                    manifest,
                    account,
                    selected,
                    output,
                    log,
                    cancel,
                    convert_pptx=convert_pptx,
                )


def status(account: Account, state_dir: Path):
    from .manifest import Manifest

    database = Path(state_dir) / "accounts" / account.slug / "manifest.sqlite3"
    if not database.exists():
        return []
    with Manifest(database) as manifest:
        return manifest.status()


def forget_login(account: Account, state_dir: Path) -> None:
    """Remove only this account's authentication; retain history and materials."""
    with account_lock(state_dir, account):
        directory = Path(state_dir) / "accounts" / account.slug
        for name in ("storage_state.json", "session_account.json"):
            (directory / name).unlink(missing_ok=True)
        settings = Settings(state_dir)
        config = settings.load()
        if config.get("account") == account.key:
            config.pop("account", None)
        config.get("selected", {}).pop(account.key, None)
        settings.save(config)
