"""Safe, bounded PowerPoint (PPT/PPTX) to PDF conversion via PowerPoint or LibreOffice."""

from __future__ import annotations

import contextlib
import csv
import gc
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from .browser_client import MAX_BYTES
from .models import ConverterInfo, InvalidContentError
from .paths import choose_destination

POWERPOINT_EXTENSIONS = {".ppt", ".pptx"}
POWERPOINT_CONTENT_TYPES = {
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3
PP_ALERTS_NONE = 1
PP_SAVE_AS_PDF = 32


def is_powerpoint(path: Path | str, filename: str = "", content_type: str = "") -> bool:
    """Return True when the file path, original filename or MIME type is PPT/PPTX."""
    if Path(path).suffix.lower() in POWERPOINT_EXTENSIONS:
        return True
    if filename and Path(filename).suffix.lower() in POWERPOINT_EXTENSIONS:
        return True
    lowered = content_type.lower()
    return any(mime in lowered for mime in POWERPOINT_CONTENT_TYPES)


def _extract_local_server_executable(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 1:
            return text[1:end]
    match = re.match(r"^([A-Za-z]:\\.*?\.exe)\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return text.split()[0]


def _detect_powerpoint() -> ConverterInfo | None:
    if os.name != "nt":
        return None
    if importlib.util.find_spec("pythoncom") is None or importlib.util.find_spec("win32com") is None:
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"PowerPoint.Application\CLSID") as key:
            clsid = str(winreg.QueryValue(key, None)).strip()
        if not clsid:
            return None
        executable = ""
        for subkey in (
            rf"CLSID\{clsid}\LocalServer32",
            rf"WOW6432Node\CLSID\{clsid}\LocalServer32",
        ):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, subkey) as srv_key:
                    raw = str(winreg.QueryValue(srv_key, None))
                candidate = _extract_local_server_executable(raw)
                if candidate and Path(candidate).is_file():
                    executable = str(Path(candidate))
                    break
            except OSError:
                continue
        return ConverterInfo(
            available=True,
            backend="powerpoint",
            detail="Microsoft PowerPoint (PowerPoint.Application COM)",
            executable=executable,
        )
    except OSError:
        return None


def _detect_libreoffice() -> ConverterInfo | None:
    candidates: list[str] = []
    for name in ("soffice.com", "soffice.exe", "soffice"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    if os.name == "nt":
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_name)
            if base:
                candidates.append(str(Path(base) / "LibreOffice" / "program" / "soffice.com"))
                candidates.append(str(Path(base) / "LibreOffice" / "program" / "soffice.exe"))
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            resolved = str(path.resolve())
            return ConverterInfo(
                available=True,
                backend="libreoffice",
                detail=f"LibreOffice ({path.name})",
                executable=resolved,
            )
    return None


def detect_converter() -> ConverterInfo:
    """Detect an installed local presentation-to-PDF converter without launching it."""
    powerpoint = _detect_powerpoint()
    if powerpoint is not None:
        return powerpoint
    libreoffice = _detect_libreoffice()
    if libreoffice is not None:
        return libreoffice
    return ConverterInfo(
        available=False,
        backend="none",
        detail="Unavailable (install Microsoft PowerPoint or LibreOffice)",
        executable="",
    )


def validate_pdf_file(path: Path) -> tuple[str, int]:
    """Verify that a converted output is a non-empty, structurally bounded PDF file."""
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise InvalidContentError("Converted PDF file is missing.")
    size = target.stat().st_size
    if size <= 0 or size > MAX_BYTES:
        raise InvalidContentError("Converted PDF is empty or exceeds the 150 MiB limit.")
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        header = stream.read(8)
        if not header.startswith(b"%PDF-"):
            raise InvalidContentError("Converted output does not start with a PDF header.")
        stream.seek(0)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        stream.seek(max(0, size - 2048))
        tail = stream.read(2048)
    if b"%%EOF" not in tail:
        raise InvalidContentError("Converted PDF is incomplete (missing %%EOF trailer).")
    return digest.hexdigest(), size


def _running_powerpoint_pids() -> set[int]:
    if os.name != "nt":
        return set()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq POWERPNT.EXE", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=flags,
            check=False,
        )
    except Exception:  # noqa: BLE001
        return set()
    pids: set[int] = set()
    for row in csv.reader(io.StringIO(completed.stdout)):
        if len(row) >= 2 and row[0].strip().lower() == "powerpnt.exe" and row[1].strip().isdigit():
            pids.add(int(row[1].strip()))
    return pids


def _pid_from_hwnd(hwnd: int) -> int:
    if hwnd <= 0 or os.name != "nt":
        return 0
    with contextlib.suppress(Exception):
        import win32process

        _tid, pid = win32process.GetWindowThreadProcessId(int(hwnd))
        if isinstance(pid, int) and pid > 0:
            return pid
    with contextlib.suppress(Exception):
        import ctypes
        import ctypes.wintypes

        pid_dword = ctypes.wintypes.DWORD(0)
        ctypes.windll.user32.GetWindowThreadProcessId(
            ctypes.wintypes.HWND(int(hwnd)), ctypes.byref(pid_dword)
        )
        return int(pid_dword.value)
    return 0


def _find_powerpoint_frame_windows() -> list[tuple[int, int]]:
    """Return (hwnd, pid) pairs for top-level PowerPoint frame windows."""
    if os.name != "nt":
        return []
    results: list[tuple[int, int]] = []
    with contextlib.suppress(Exception):
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        hwnd = 0
        while True:
            hwnd = int(
                user32.FindWindowExW(
                    ctypes.wintypes.HWND(0),
                    ctypes.wintypes.HWND(hwnd),
                    "PPTFrameClass",
                    None,
                )
            )
            if not hwnd:
                break
            pid = _pid_from_hwnd(hwnd)
            if pid > 0:
                results.append((hwnd, pid))
    return results


def _resolve_powerpoint_app_pid(app, before_pids: set[int]) -> tuple[int, int]:
    """Resolve the exact (hwnd, pid) for our automation instance or refuse uncertain ownership."""
    current_pids = _running_powerpoint_pids()
    new_pids = current_pids - before_pids
    if before_pids or len(current_pids) > 1 or len(new_pids) > 1:
        raise RuntimeError(
            "Uncertain PowerPoint process ownership; another PowerPoint session is active."
        )
    hwnd = 0
    with contextlib.suppress(Exception):
        raw_hwnd = getattr(app, "HWND", None)
        if isinstance(raw_hwnd, int) and raw_hwnd > 0:
            hwnd = raw_hwnd
        elif callable(raw_hwnd):
            candidate = raw_hwnd()
            if isinstance(candidate, int) and candidate > 0:
                hwnd = candidate
    pid = _pid_from_hwnd(hwnd) if hwnd > 0 else 0
    if pid <= 0:
        frames = [
            (frame_hwnd, frame_pid)
            for frame_hwnd, frame_pid in _find_powerpoint_frame_windows()
            if frame_pid not in before_pids
        ]
        frame_pids = {frame_pid for _, frame_pid in frames}
        if len(frame_pids) != 1:
            raise RuntimeError(
                "Could not uniquely identify the PowerPoint helper window (HWND) and process ID."
            )
        hwnd, pid = frames[0]
    after_pids = _running_powerpoint_pids()
    after_new = after_pids - before_pids
    if pid <= 0 or pid in before_pids or len(after_pids) > 1 or (after_new and after_new != {pid}):
        raise RuntimeError(
            "Uncertain PowerPoint process ownership; refusing to proceed to protect user work."
        )
    return hwnd, pid


def _write_owned_helper_pid(control_file: Path, hwnd: int, pid: int) -> None:
    control_file.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=control_file.parent, prefix="helper-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"hwnd": int(hwnd), "pid": int(pid)}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, control_file)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _read_owned_helper_pid(control_file: Path | None) -> int | None:
    if control_file is None or not control_file.is_file():
        return None
    try:
        payload = json.loads(control_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = payload.get("pid") if isinstance(payload, dict) else None
    return pid if isinstance(pid, int) and pid > 0 else None


def _has_visible_window(pid: int) -> bool:
    """Protect an owned helper if the user has since opened it interactively."""
    try:
        import win32gui
        import win32process

        visible = []

        def inspect(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                visible.append(hwnd)

        win32gui.EnumWindows(inspect, None)
        return bool(visible)
    except Exception:  # noqa: BLE001 -- uncertain visibility must preserve user work
        return True


def _cleanup_owned_powerpoint_pid(owned_pid: int | None, wait_seconds: float = 2.0) -> None:
    """Terminate only the positively identified owned helper PID if it remains running."""
    if os.name != "nt" or not owned_pid or owned_pid <= 0:
        return
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while owned_pid in _running_powerpoint_pids() and time.monotonic() < deadline:
        time.sleep(0.1)
    if owned_pid not in _running_powerpoint_pids():
        return
    if _has_visible_window(owned_pid):
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with contextlib.suppress(Exception):
        subprocess.run(
            ["taskkill", "/PID", str(owned_pid), "/T", "/F"],
            capture_output=True,
            timeout=5,
            creationflags=flags,
            check=False,
        )


def _powerpoint_worker_command(
    source: Path,
    temp_pdf: Path,
    error_file: Path,
    control_file: Path | None = None,
) -> list[str]:
    args = [
        "_convert-powerpoint",
        str(source),
        str(temp_pdf),
        str(error_file),
    ]
    if control_file is not None:
        args.append(str(control_file))
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "egeders_downloader.cli", *args]


def _powerpoint_com_export(
    source: Path, target: Path, control_file: Path | None = None
) -> int:
    import pythoncom
    import win32com.client

    before_pids = _running_powerpoint_pids()
    if before_pids:
        raise RuntimeError(
            "Microsoft PowerPoint is already open; close PowerPoint or install LibreOffice to convert presentations safely."
        )
    pythoncom.CoInitialize()
    app = None
    presentation = None
    owned_app = False
    owned_pid = 0
    try:
        already_active = False
        with contextlib.suppress(Exception):
            active = win32com.client.GetActiveObject("PowerPoint.Application")
            del active
            already_active = True
        if already_active:
            raise RuntimeError(
                "Microsoft PowerPoint is already open; close PowerPoint or install LibreOffice to convert presentations safely."
            )
        app = win32com.client.DispatchEx("PowerPoint.Application")
        if bool(getattr(app, "Visible", 0)) or int(getattr(app.Presentations, "Count", 0)) > 0:
            raise RuntimeError(
                "Microsoft PowerPoint is already open; close PowerPoint or install LibreOffice to convert presentations safely."
            )
        hwnd, owned_pid = _resolve_powerpoint_app_pid(app, before_pids)
        owned_app = True
        if control_file is not None:
            _write_owned_helper_pid(control_file, hwnd, owned_pid)
        with contextlib.suppress(Exception):
            app.DisplayAlerts = PP_ALERTS_NONE
        # Force-disable all macros before opening any presentation file.
        app.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
        # Open(FileName, ReadOnly=-1, Untitled=0, WithWindow=0)
        presentation = app.Presentations.Open(str(source), -1, 0, 0)
        presentation.SaveAs(str(target), PP_SAVE_AS_PDF)
        return owned_pid
    finally:
        if presentation is not None:
            with contextlib.suppress(Exception):
                presentation.Close()
            del presentation
        if app is not None:
            if owned_app:
                try:
                    in_use = bool(getattr(app, "Visible", 0)) or int(getattr(app.Presentations, "Count", 0)) > 0
                except Exception:  # noqa: BLE001
                    in_use = False
                if not in_use:
                    with contextlib.suppress(Exception):
                        app.Quit()
            del app
        gc.collect()
        pythoncom.CoUninitialize()


def run_powerpoint_worker(argv: list[str]) -> int:
    """Internal subprocess entry point for bounded PowerPoint COM conversion."""
    if len(argv) < 2 or len(argv) > 4:
        return 2
    source = Path(argv[0]).resolve()
    target = Path(argv[1]).resolve()
    error_file = Path(argv[2]).resolve() if len(argv) >= 3 and argv[2] else None
    control_file = Path(argv[3]).resolve() if len(argv) >= 4 and argv[3] else None
    try:
        _powerpoint_com_export(source, target, control_file=control_file)
        return 0
    except Exception as exc:  # noqa: BLE001 -- report worker exception cleanly to parent
        message = str(exc) or exc.__class__.__name__
        if error_file is not None:
            with contextlib.suppress(OSError):
                error_file.write_text(message, encoding="utf-8")
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        return 1


def _convert_with_powerpoint(
    source: Path,
    temp_pdf: Path,
    error_file: Path,
    timeout_seconds: float,
    control_file: Path | None = None,
) -> None:
    if _running_powerpoint_pids():
        raise RuntimeError(
            "Microsoft PowerPoint is already open; close PowerPoint or install LibreOffice to convert presentations safely."
        )
    ctrl_path = control_file if control_file is not None else error_file.with_name("helper.json")
    command = _powerpoint_worker_command(source, temp_pdf, error_file, ctrl_path)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            command,
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            creationflags=flags,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        owned_pid = _read_owned_helper_pid(ctrl_path)
        _cleanup_owned_powerpoint_pid(owned_pid, wait_seconds=0.0)
        raise RuntimeError(
            f"PowerPoint conversion timed out after {timeout_seconds:g}s."
        ) from exc
    owned_pid = _read_owned_helper_pid(ctrl_path)
    _cleanup_owned_powerpoint_pid(owned_pid, wait_seconds=2.0)
    if completed.returncode != 0:
        detail = ""
        if error_file.is_file():
            try:
                detail = error_file.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                detail = ""
        if not detail:
            detail = (completed.stderr or completed.stdout or f"exit code {completed.returncode}").strip()
        raise RuntimeError(f"PowerPoint conversion failed: {detail}")


def _convert_with_libreoffice(
    source: Path, temp_pdf: Path, executable: str, timeout_seconds: float
) -> None:
    if not executable:
        raise RuntimeError("LibreOffice executable path is not configured.")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="egeders-lo-") as tmp:
        tmp_root = Path(tmp)
        profile_dir = tmp_root / "profile"
        out_dir = tmp_root / "out"
        profile_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        profile_uri = profile_dir.resolve().as_uri()
        command = [
            executable,
            f"-env:UserInstallation={profile_uri}",
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--nolockcheck",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(source),
        ]
        try:
            completed = subprocess.run(
                command,
                timeout=timeout_seconds,
                capture_output=True,
                text=True,
                creationflags=flags,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"LibreOffice conversion timed out after {timeout_seconds:g}s."
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or f"exit code {completed.returncode}").strip()
            raise RuntimeError(f"LibreOffice conversion failed: {detail}")
        produced = out_dir / f"{source.stem}.pdf"
        if not produced.is_file():
            pdfs = list(out_dir.glob("*.pdf"))
            if len(pdfs) != 1:
                raise RuntimeError("LibreOffice did not produce a PDF output file.")
            produced = pdfs[0]
        shutil.copyfile(produced, temp_pdf)


def convert_to_pdf_atomic(
    source: Path,
    directory: Path,
    source_filename: str,
    resource_id: int,
    source_sha256: str,
    converter: ConverterInfo | Callable[[Path, Path], None] | None = None,
    timeout_seconds: float = 120.0,
) -> tuple[Path, str, int, ConverterInfo]:
    """Convert a presentation to PDF, validate it, and publish atomically without overwrites."""
    source_path = Path(source).resolve()
    if source_path.is_symlink() or not source_path.is_file():
        raise ValueError("Source presentation file is missing.")
    size = source_path.stat().st_size
    if size <= 0 or size > MAX_BYTES:
        raise ValueError("Source presentation is empty or exceeds the 150 MiB limit.")
    target_dir = Path(directory).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    custom_fn: Callable[[Path, Path], None] | None = None
    if callable(converter):
        custom_fn = converter
        info = ConverterInfo(
            available=True,
            backend=str(getattr(converter, "backend", "custom")),
            detail="Custom converter",
        )
    else:
        info = converter if converter is not None else detect_converter()
    if not info.available:
        raise RuntimeError(info.detail or "No PowerPoint/LibreOffice converter is available.")
    temp_dir = Path(tempfile.mkdtemp(prefix="convert-", dir=target_dir))
    try:
        temp_pdf = temp_dir / "converted.pdf"
        if custom_fn is not None:
            custom_fn(source_path, temp_pdf)
        elif info.backend == "powerpoint":
            if _running_powerpoint_pids():
                fallback = _detect_libreoffice()
                if fallback is not None:
                    info = fallback
                    _convert_with_libreoffice(
                        source_path, temp_pdf, info.executable, timeout_seconds
                    )
                else:
                    raise RuntimeError(
                        "Microsoft PowerPoint is already open; close PowerPoint or install LibreOffice to convert presentations safely."
                    )
            else:
                _convert_with_powerpoint(
                    source_path,
                    temp_pdf,
                    temp_dir / "error.txt",
                    timeout_seconds,
                    control_file=temp_dir / "helper.json",
                )
        elif info.backend == "libreoffice":
            _convert_with_libreoffice(source_path, temp_pdf, info.executable, timeout_seconds)
        else:
            raise RuntimeError(f"Unsupported converter backend: {info.backend}")
        pdf_sha256, pdf_size = validate_pdf_file(temp_pdf)
        with temp_pdf.open("r+b") as stream:
            os.fsync(stream.fileno())
        stem = Path(source_filename).stem if source_filename else source_path.stem
        pdf_filename = f"{stem or 'presentation'}.pdf"
        destination = choose_destination(target_dir, pdf_filename, resource_id, source_sha256)
        for _ in range(5):
            try:
                if os.name == "nt":
                    os.rename(temp_pdf, destination)
                else:
                    os.link(temp_pdf, destination)
                    temp_pdf.unlink()
                break
            except FileExistsError:
                destination = choose_destination(target_dir, pdf_filename, resource_id, source_sha256)
        else:
            raise RuntimeError("Repeated PDF filename collisions; retry sync.")
        return destination, pdf_sha256, pdf_size, info
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
