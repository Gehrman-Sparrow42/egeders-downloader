"""Command-line access to login, discovery and repeatable material syncs."""

import argparse
import getpass
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from . import service
from .models import Account, AuthExpiredError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download selected Ege Ders course resources."
    )
    parser.add_argument("--account", help="University username or username@domain")
    parser.add_argument(
        "--state-dir", type=Path, help="Local settings, session and manifest directory"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    login = commands.add_parser(
        "login", help="Sign in and save a reusable browser session"
    )
    login.add_argument(
        "--manual", action="store_true", help="Complete login yourself in the browser"
    )
    courses = commands.add_parser("courses", help="Discover accessible courses")
    courses.add_argument("--json", action="store_true")
    sync = commands.add_parser(
        "sync", help="Sync specified courses or the selection saved in the GUI"
    )
    sync.add_argument(
        "--course",
        type=int,
        nargs="+",
        action="extend",
        help="One or more course IDs; repeatable",
    )
    sync.add_argument("--output", type=Path, help="Absolute output root")
    sync.add_argument("--json", action="store_true")
    status = commands.add_parser("status", help="Show locally recorded resource status")
    status.add_argument("--json", action="store_true")
    commands.add_parser("schedule-help", help="Print Task Scheduler setup instructions")
    return parser


def schedule_help() -> str:
    return """Windows Task Scheduler (no task is created by this command):
1. Log in once and choose courses in the GUI. Run a manual sync first.
2. Create a Basic Task, choose a schedule, and select Start a program.
   Program: C:\\Tools\\.venv\\Scripts\\python.exe
   Arguments: -m egeders_downloader.cli --account USER@ogrenci.ege.edu.tr sync
   Start in: C:\\Tools\\egeders_downloader
3. Use the same Windows user that saved the session. Enable 'Do not start a
   new instance' to avoid overlapping syncs. No password is stored by this app.
4. For a custom state directory, put --state-dir C:\\absolute\\state before sync.
   Saved output and course selection are reused. Expired login exits with code 2;
   rerun login interactively and retry. Other failures exit 1. Success exits 0.
Automatic re-login after session expiry is not included in this version."""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "schedule-help":
        print(schedule_help())
        return 0
    try:
        settings = service.Settings(args.state_dir)
        config = settings.load()
        account_text = args.account or config.get("account", "")
        if not account_text:
            raise ValueError(
                "Specify --account USER@ogrenci.ege.edu.tr before the command, or save an account in the GUI."
            )
        account = Account(account_text)
        log = lambda message: print(message, file=sys.stderr)
        if args.command == "login":
            password = None
            if not args.manual:
                password = os.environ.pop("EGEDERS_PASSWORD", None)
                if password is None:
                    if not sys.stdin.isatty():
                        raise ValueError(
                            "No interactive terminal. Run login --manual interactively, or set EGEDERS_PASSWORD for this process."
                        )
                    password = getpass.getpass("University password: ")
                if not password:
                    raise ValueError("Password cannot be empty.")
            try:
                service.login(
                    account,
                    settings.state_dir,
                    password=password,
                    manual=args.manual,
                    log=log,
                )
            finally:
                password = None
            config["account"] = account.key
            settings.save(config)
            print("Login saved. Run courses to discover course IDs.")
        elif args.command == "courses":
            courses = service.discover(account, settings.state_dir, log=log)
            if args.json:
                print(
                    json.dumps(
                        [asdict(course) for course in courses],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                for course in courses:
                    print(f"{course.id}\t{course.semester or '-'}\t{course.title}")
        elif args.command == "status":
            rows = service.status(account, settings.state_dir)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
            else:
                for row in rows:
                    print(" | ".join(f"{key}: {value}" for key, value in row.items()))
                if not rows:
                    print("No downloaded resources recorded for this account.")
        elif args.command == "sync":
            course_ids = args.course or config.get("selected", {}).get(account.key, [])
            if not course_ids:
                raise ValueError(
                    "No courses selected. Supply --course ID, or save a selection in the GUI."
                )
            if any(
                not isinstance(course_id, int) or course_id <= 0
                for course_id in course_ids
            ):
                raise ValueError("Course IDs must be positive integers.")
            output = args.output or Path(config.get("output") or service.DEFAULT_OUTPUT)
            if not output.is_absolute():
                raise ValueError("Output must be an absolute path.")
            summary = service.sync(
                account,
                settings.state_dir,
                output,
                list(dict.fromkeys(course_ids)),
                log=log,
            )
            if args.json:
                print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
            else:
                print(
                    f"New: {summary.new}; updated: {summary.updated}; unchanged: {summary.unchanged}; failed: {summary.failed}; unsupported: {summary.unsupported}; cancelled: {summary.cancelled}"
                )
                for error in summary.errors:
                    print(error, file=sys.stderr)
            return 1 if summary.failed or summary.cancelled else 0
        return 0
    except AuthExpiredError as exc:
        print(
            f"Login required: {exc}\nRun: python -m egeders_downloader.cli --account {account.key} login --manual",
            file=sys.stderr,
        )
        return 2
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
