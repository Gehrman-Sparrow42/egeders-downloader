"""Application entry points shared by GUI and CLI."""

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from .models import Account, AuthExpiredError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE = PROJECT_ROOT / ".local"
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


def sync(
    account: Account,
    state_dir: Path,
    output: Path,
    course_ids: list[int],
    log=print,
    cancel: threading.Event | None = None,
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
                    client, manifest, account, selected, output, log, cancel
                )


def status(account: Account, state_dir: Path):
    from .manifest import Manifest

    database = Path(state_dir) / "accounts" / account.slug / "manifest.sqlite3"
    if not database.exists():
        return []
    with Manifest(database) as manifest:
        return manifest.status()
