"""Bounded, Windows-safe names and non-destructive destination selection."""

import re
import unicodedata
from pathlib import Path

from .models import Account, Course, Resource

_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$", re.IGNORECASE)


def safe_component(text: str, max_length: int = 80) -> str:
    if max_length < 2:
        raise ValueError("Component limit must be at least 2.")
    text = unicodedata.normalize("NFC", str(text))
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", text)
    text = "".join("_" if unicodedata.category(char) == "Cf" else char for char in text)
    text = re.sub(r"\s+", " ", text).strip(" .")[:max_length].rstrip(" .") or "untitled"
    if _RESERVED.fullmatch(text.split(".")[0]):
        text = "_" + text
    return text[:max_length].rstrip(" .")


def resource_directory(output: Path, account: Account, course: Course, resource: Resource) -> Path:
    root = Path(output).resolve()
    # Leave space for the final filename even with ordinary Windows path limits.
    remaining = 230 - len(str(root)) - len(account.slug) - 3 - 85
    course_suffix = f"__{course.id}"
    if remaining < len(course_suffix) + 10:
        raise ValueError("Output directory is too long; choose a shorter output path.")
    course_limit = min(60, remaining - 8)
    section_limit = min(40, remaining - course_limit)
    course_name = safe_component(course.title, course_limit - len(course_suffix)) + course_suffix
    section_name = safe_component(resource.section or "General", section_limit)
    return root / account.slug / course_name / section_name


def choose_destination(directory: Path, filename: str, resource_id: int, sha256: str) -> Path:
    """Return a free name; caller must publish with exclusive creation to avoid races."""
    if resource_id <= 0 or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise ValueError("A positive resource ID and SHA-256 digest are required.")
    directory = Path(directory)
    # Preserve the extension even when the server supplied a very long name.
    raw_extension = Path(filename).suffix
    extension = safe_component(raw_extension, 12) if raw_extension else ""
    if extension and not extension.startswith("."):
        extension = "." + extension
    stem = safe_component(filename[:-len(raw_extension)] if raw_extension else filename, 80)
    marker = f"__cmid{resource_id}_{sha256[:12]}"
    for counter in range(100000):
        suffix = marker + (f"_{counter}" if counter else "") + extension
        budget = min(85, 239 - len(str(directory.resolve())) - 1)
        if budget <= len(suffix) + 2:
            raise ValueError("Destination path is too long; choose a shorter output path.")
        candidate = directory / (safe_component(stem, budget - len(suffix)) + suffix)
        if not candidate.exists():
            return candidate
    raise FileExistsError("Too many destination name collisions.")
