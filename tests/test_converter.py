"""Unit and real-PowerPoint synthetic conversion tests."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from egeders_downloader import converter
from egeders_downloader.converter import (
    MSO_AUTOMATION_SECURITY_FORCE_DISABLE,
    PP_ALERTS_NONE,
    PP_SAVE_AS_PDF,
    convert_to_pdf_atomic,
    detect_converter,
    is_powerpoint,
    run_powerpoint_worker,
    validate_pdf_file,
)
from egeders_downloader.models import InvalidContentError


def test_is_powerpoint_recognizes_extensions_and_mime_types():
    assert is_powerpoint("Lecture1.pptx")
    assert is_powerpoint("Lecture1.PPT")
    assert is_powerpoint("material.bin", filename="slides.pptx")
    assert is_powerpoint(
        "material.bin",
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    assert is_powerpoint("material.bin", content_type="application/vnd.ms-powerpoint")
    assert not is_powerpoint("notes.pdf", filename="notes.pdf", content_type="application/pdf")
    assert not is_powerpoint("archive.zip", filename="archive.zip", content_type="application/zip")


def test_validate_pdf_file_checks_header_and_eof_trailer(tmp_path):
    valid = tmp_path / "valid.pdf"
    valid.write_bytes(b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n")
    digest, size = validate_pdf_file(valid)
    assert len(digest) == 64
    assert size == len(valid.read_bytes())

    not_pdf = tmp_path / "not_pdf.pdf"
    not_pdf.write_bytes(b"PK\x03\x04not a pdf")
    with pytest.raises(InvalidContentError, match="PDF header"):
        validate_pdf_file(not_pdf)

    truncated = tmp_path / "truncated.pdf"
    truncated.write_bytes(b"%PDF-1.7\ntruncated without trailer")
    with pytest.raises(InvalidContentError, match="incomplete"):
        validate_pdf_file(truncated)


def test_powerpoint_com_persists_hwnd_pid_before_open_and_quits_owned_instance(
    tmp_path, monkeypatch
):
    events = []
    control_file = tmp_path / "helper.json"

    class FakePresentation:
        def SaveAs(self, target, fmt):
            events.append(("SaveAs", target, fmt))
            Path(target).write_bytes(b"%PDF-1.7\n%%EOF\n")

        def Close(self):
            events.append(("Close",))

    class FakePresentations:
        Count = 0

        def Open(self, filename, read_only, untitled, with_window):
            # Control file must already exist with the owned PID before Open is called.
            recorded = json.loads(control_file.read_text(encoding="utf-8"))
            events.append(("ControlBeforeOpen", recorded["hwnd"], recorded["pid"]))
            events.append(("Open", filename, read_only, untitled, with_window))
            return FakePresentation()

    class FakeApp:
        HWND = 98765
        Visible = 0
        DisplayAlerts = 2
        AutomationSecurity = 1
        Presentations = FakePresentations()

        def Quit(self):
            events.append(("Quit",))

    fake_app = FakeApp()
    running = [set(), {4242}, {4242}]
    monkeypatch.setattr(
        converter, "_running_powerpoint_pids", lambda: running.pop(0) if running else {4242}
    )
    fake_win32 = SimpleNamespace(
        client=SimpleNamespace(
            GetActiveObject=lambda _progid: (_ for _ in ()).throw(RuntimeError("no active")),
            DispatchEx=lambda _progid: fake_app,
        )
    )
    fake_pythoncom = SimpleNamespace(
        CoInitialize=lambda: events.append(("CoInitialize",)),
        CoUninitialize=lambda: events.append(("CoUninitialize",)),
    )
    fake_win32process = SimpleNamespace(
        GetWindowThreadProcessId=lambda hwnd: (10, 4242 if hwnd == 98765 else 0)
    )
    monkeypatch.setitem(sys.modules, "win32com", fake_win32)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_win32.client)
    monkeypatch.setitem(sys.modules, "pythoncom", fake_pythoncom)
    monkeypatch.setitem(sys.modules, "win32process", fake_win32process)

    src = tmp_path / "deck.pptx"
    dst = tmp_path / "deck.pdf"
    src.write_bytes(b"PK\x03\x04")
    assert run_powerpoint_worker([str(src), str(dst), str(tmp_path / "err.txt"), str(control_file)]) == 0

    assert fake_app.AutomationSecurity == MSO_AUTOMATION_SECURITY_FORCE_DISABLE
    assert fake_app.DisplayAlerts == PP_ALERTS_NONE
    assert ("ControlBeforeOpen", 98765, 4242) in events
    assert ("Open", str(src), -1, 0, 0) in events
    assert ("SaveAs", str(dst), PP_SAVE_AS_PDF) in events
    assert ("Close",) in events
    assert ("Quit",) in events
    assert ("CoUninitialize",) in events


def test_preexisting_powerpoint_is_never_attached_modified_or_closed(tmp_path, monkeypatch):
    class ExistingUserApp:
        Visible = 1
        DisplayAlerts = 2
        AutomationSecurity = 1
        Presentations = SimpleNamespace(
            Count=1,
            Open=lambda *_args: pytest.fail("Must never open slides in existing user session"),
        )

        def __setattr__(self, name, value):
            if name in {"DisplayAlerts", "AutomationSecurity"}:
                pytest.fail(f"Must never mutate {name} on existing user PowerPoint session")
            super().__setattr__(name, value)

        def Quit(self):
            pytest.fail("Existing user PowerPoint session must never be quit")

    user_app = ExistingUserApp()
    monkeypatch.setattr(converter, "_running_powerpoint_pids", lambda: set())
    fake_win32 = SimpleNamespace(
        client=SimpleNamespace(
            GetActiveObject=lambda _progid: user_app,
            DispatchEx=lambda _progid: pytest.fail("Should not dispatch when active session exists"),
        )
    )
    fake_pythoncom = SimpleNamespace(
        CoInitialize=lambda: None,
        CoUninitialize=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "win32com", fake_win32)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_win32.client)
    monkeypatch.setitem(sys.modules, "pythoncom", fake_pythoncom)

    src = tmp_path / "deck.pptx"
    dst = tmp_path / "deck.pdf"
    src.write_bytes(b"PK\x03\x04")
    with pytest.raises(RuntimeError, match="already open"):
        converter._powerpoint_com_export(src, dst)
    assert user_app.AutomationSecurity == 1
    assert user_app.DisplayAlerts == 2


def test_uncertain_ownership_race_during_dispatch_aborts_without_quitting_or_opening(
    tmp_path, monkeypatch
):
    class RacedApp:
        HWND = 55555
        Visible = 0
        DisplayAlerts = 2
        AutomationSecurity = 1
        Presentations = SimpleNamespace(
            Count=0,
            Open=lambda *_args: pytest.fail("Must not open presentation when ownership is uncertain"),
        )

        def Quit(self):
            pytest.fail("Must not Quit when another PowerPoint process raced during dispatch")

    # Initially no PIDs; during DispatchEx both helper (1111) and user (2222) appear.
    snapshots = [set(), {1111, 2222}]
    monkeypatch.setattr(
        converter, "_running_powerpoint_pids", lambda: snapshots.pop(0) if snapshots else {1111, 2222}
    )
    fake_win32 = SimpleNamespace(
        client=SimpleNamespace(
            GetActiveObject=lambda _progid: (_ for _ in ()).throw(RuntimeError("no active")),
            DispatchEx=lambda _progid: RacedApp(),
        )
    )
    fake_pythoncom = SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None)
    monkeypatch.setitem(sys.modules, "win32com", fake_win32)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_win32.client)
    monkeypatch.setitem(sys.modules, "pythoncom", fake_pythoncom)

    src = tmp_path / "deck.pptx"
    dst = tmp_path / "deck.pdf"
    ctrl = tmp_path / "helper.json"
    src.write_bytes(b"PK\x03\x04")
    with pytest.raises(RuntimeError, match="Uncertain PowerPoint process ownership"):
        converter._powerpoint_com_export(src, dst, control_file=ctrl)
    assert not ctrl.exists()


def test_timeout_kills_only_positively_owned_pid_and_spares_new_user_pid(tmp_path, monkeypatch):
    killed = []
    # Initially no PowerPoint running; during conversion owned helper 1111 and user 2222 are running.
    snapshots = [set(), {1111, 2222}, {1111, 2222}]
    monkeypatch.setattr(
        converter, "_running_powerpoint_pids", lambda: snapshots.pop(0) if snapshots else {1111, 2222}
    )

    def fake_run(command, **kwargs):
        if command[0] == "taskkill":
            killed.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        # Worker writes owned helper PID 1111 to control_file, then times out.
        ctrl = Path(command[-1])
        ctrl.write_text(json.dumps({"hwnd": 1234, "pid": 1111}), encoding="utf-8")
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 10))

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="timed out"):
        converter._convert_with_powerpoint(
            tmp_path / "in.pptx",
            tmp_path / "out.pdf",
            tmp_path / "error.txt",
            5.0,
            control_file=tmp_path / "helper.json",
        )
    assert killed == [["taskkill", "/PID", "1111", "/T", "/F"]]


def test_owned_helper_opened_interactively_is_preserved(monkeypatch):
    killed = []
    monkeypatch.setattr(converter, "_running_powerpoint_pids", lambda: {1111})
    monkeypatch.setattr(converter, "_has_visible_window", lambda pid: True)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: killed.append(args))
    converter._cleanup_owned_powerpoint_pid(1111, wait_seconds=0)
    assert killed == []


def test_timeout_without_identified_helper_pid_kills_nothing(tmp_path, monkeypatch):
    killed = []
    # User opened PID 2222 during conversion, and worker timed out before writing helper.json.
    snapshots = [set(), {2222}]
    monkeypatch.setattr(
        converter, "_running_powerpoint_pids", lambda: snapshots.pop(0) if snapshots else {2222}
    )

    def fake_run(command, **kwargs):
        if command[0] == "taskkill":
            killed.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 10))

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="timed out"):
        converter._convert_with_powerpoint(
            tmp_path / "in.pptx",
            tmp_path / "out.pdf",
            tmp_path / "error.txt",
            5.0,
            control_file=tmp_path / "helper.json",
        )
    assert killed == []


def test_libreoffice_uses_isolated_profile_and_timeout(tmp_path, monkeypatch):
    recorded_commands = []
    src = tmp_path / "slides.pptx"
    src.write_bytes(b"PK\x03\x04")
    out_pdf = tmp_path / "out.pdf"

    def fake_run(command, **kwargs):
        recorded_commands.append((command, kwargs.get("timeout")))
        outdir = Path(command[command.index("--outdir") + 1])
        (outdir / "slides.pdf").write_bytes(b"%PDF-1.7\nlibreoffice\n%%EOF\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    converter._convert_with_libreoffice(src, out_pdf, "C:/LibreOffice/program/soffice.com", 45.0)
    assert out_pdf.read_bytes() == b"%PDF-1.7\nlibreoffice\n%%EOF\n"
    assert len(recorded_commands) == 1
    cmd, timeout = recorded_commands[0]
    assert timeout == 45.0
    assert "--headless" in cmd
    assert any(arg.startswith("-env:UserInstallation=file:///") for arg in cmd)


@pytest.mark.skipif(
    detect_converter().backend != "powerpoint" or bool(converter._running_powerpoint_pids()),
    reason="Requires registered PowerPoint with no active user session",
)
def test_real_powerpoint_converts_synthetic_pptx_and_ppt(tmp_path):
    pptx_source = tmp_path / "Synthetic_Lecture.pptx"
    ppt_source = tmp_path / "Synthetic_Legacy.ppt"
    script = (
        "import gc, sys, pythoncom, win32com.client\n"
        "pythoncom.CoInitialize()\n"
        "app = win32com.client.DispatchEx('PowerPoint.Application')\n"
        "try:\n"
        "    app.DisplayAlerts = 1\n"
        "    app.AutomationSecurity = 3\n"
        "    pres = app.Presentations.Add(0)\n"
        "    slide = pres.Slides.Add(1, 1)\n"
        "    slide.Shapes.Title.TextFrame.TextRange.Text = 'Ege Ders Synthetic Slide'\n"
        "    pres.SaveAs(sys.argv[1], 24)\n"
        "    pres.SaveAs(sys.argv[2], 1)\n"
        "    pres.Close()\n"
        "    del slide, pres\n"
        "finally:\n"
        "    app.Quit()\n"
        "    del app\n"
        "    gc.collect()\n"
        "    pythoncom.CoUninitialize()\n"
    )
    subprocess.run(
        [sys.executable, "-c", script, str(pptx_source), str(ppt_source)],
        check=True,
        timeout=60,
    )
    # Wait up to 2s for synthetic generator's PowerPoint instance to exit cleanly.
    for _ in range(20):
        if not converter._running_powerpoint_pids():
            break
        converter.time.sleep(0.1)
    assert not converter._running_powerpoint_pids()

    pptx_bytes = pptx_source.read_bytes()
    ppt_bytes = ppt_source.read_bytes()

    out_dir = tmp_path / "published"
    pdf1, sha1, size1, info1 = convert_to_pdf_atomic(
        pptx_source,
        out_dir,
        pptx_source.name,
        101,
        hashlib.sha256(pptx_bytes).hexdigest(),
    )
    pdf2, sha2, size2, info2 = convert_to_pdf_atomic(
        ppt_source,
        out_dir,
        ppt_source.name,
        102,
        hashlib.sha256(ppt_bytes).hexdigest(),
    )

    assert info1.backend == info2.backend == "powerpoint"
    assert pdf1.is_file() and pdf2.is_file()
    assert validate_pdf_file(pdf1) == (sha1, size1)
    assert validate_pdf_file(pdf2) == (sha2, size2)
    assert pptx_source.read_bytes() == pptx_bytes
    assert ppt_source.read_bytes() == ppt_bytes
    assert not converter._running_powerpoint_pids()
