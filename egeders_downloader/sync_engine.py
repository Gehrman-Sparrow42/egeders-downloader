"""Sequential, non-destructive synchronization with content verification."""

import hashlib
import os
import threading
from collections.abc import Callable
from pathlib import Path

from .models import Account, AuthExpiredError, Course, Summary
from .parser import parse_course
from .paths import choose_destination, resource_directory


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync_courses(
    client,
    manifest,
    account: Account,
    courses: list[Course],
    output: Path,
    log: Callable[[str], None] = print,
    cancel: threading.Event | None = None,
) -> Summary:
    """Verify remote bytes every run; retain every previous local version.

    A manifest update follows the completed filesystem write. If recording fails,
    the valid file remains on disk and can be adopted by a subsequent run.
    """
    summary = Summary()
    output = output.expanduser().resolve()
    for course in courses:
        if cancel and cancel.is_set():
            summary.cancelled = True
            break
        log(f"Course: {course.title}")
        try:
            page = parse_course(client.get_html(course.url), course.id)
        except AuthExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001 -- isolate course failures in a batch
            summary.failed += 1
            message = f"Course {course.id}: {exc}"
            summary.errors.append(message)
            log(message)
            continue
        summary.unsupported += sum(page.unsupported.values())
        if page.unsupported:
            log(
                "Other activities skipped: "
                + ", ".join(
                    f"{name}: {count}" for name, count in page.unsupported.items()
                )
            )
        if not page.resources:
            log("No supported file resources in this course.")
        for resource in page.resources:
            if cancel and cancel.is_set():
                summary.cancelled = True
                return summary
            payload = None
            try:
                directory = resource_directory(output, account, course, resource)
                directory.mkdir(parents=True, exist_ok=True)
                # The browser downloader returns a private temporary file here.
                payload = client.download(resource, directory)
                if payload.size <= 0 or not payload.temp_path.is_file():
                    raise ValueError("Download is empty or missing.")
                if payload.temp_path.stat().st_size != payload.size:
                    raise ValueError("Downloaded byte count is inconsistent.")
                if file_hash(payload.temp_path) != payload.sha256:
                    raise ValueError("Downloaded content hash is inconsistent.")
                previous = manifest.latest(resource.id)
                # Reuse historical bytes only within the requested output root.
                # Changing output therefore creates a usable copy at the new root.
                same = None
                for version in reversed(manifest.versions(resource.id)):
                    existing = Path(version["path"])
                    if (
                        version["sha256"] == payload.sha256
                        and existing.resolve().is_relative_to(output)
                        and existing.is_file()
                        and existing.stat().st_size == payload.size
                        and file_hash(existing) == payload.sha256
                    ):
                        same = existing
                        break
                if same is None:
                    # Recover a completed file after a crash/database failure
                    # between atomic publication and recording its history.
                    marker = f"__cmid{resource.id}_{payload.sha256[:12]}"
                    for candidate in directory.iterdir():
                        if (
                            marker in candidate.name
                            and not candidate.is_symlink()
                            and candidate.is_file()
                            and not candidate.name.endswith(".part")
                            and candidate.stat().st_size == payload.size
                            and file_hash(candidate) == payload.sha256
                        ):
                            same = candidate
                            break
                if same is not None:
                    manifest.record(course, resource, payload, same)
                    summary.unchanged += 1
                    log(f"Unchanged: {resource.title}")
                    continue
                destination = choose_destination(
                    directory, payload.filename, resource.id, payload.sha256
                )
                # The complete temporary file is published atomically without
                # replacing another app's file. Windows rename fails if a target
                # exists; POSIX hard-link creation provides the same guarantee.
                with payload.temp_path.open("r+b") as stream:
                    os.fsync(stream.fileno())
                for _ in range(5):
                    try:
                        if os.name == "nt":
                            os.rename(payload.temp_path, destination)
                        else:
                            os.link(payload.temp_path, destination)
                            payload.temp_path.unlink()
                        break
                    except FileExistsError:
                        destination = choose_destination(
                            directory, payload.filename, resource.id, payload.sha256
                        )
                else:
                    raise RuntimeError("Repeated filename collisions; retry sync.")
                manifest.record(course, resource, payload, destination)
                if previous:
                    summary.updated += 1
                    log(f"Saved version: {resource.title} → {destination.name}")
                else:
                    summary.new += 1
                    log(f"Downloaded: {resource.title} → {destination.name}")
            except AuthExpiredError:
                raise
            except Exception as exc:  # noqa: BLE001 -- report a resource failure and continue
                summary.failed += 1
                message = f"Resource {resource.id} ({resource.title}): {exc}"
                summary.errors.append(message)
                log(message)
            finally:
                if payload is not None:
                    payload.temp_path.unlink(missing_ok=True)
    return summary
