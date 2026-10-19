from pathlib import Path

import pytest

from egeders_downloader.models import Account, Course, Resource
from egeders_downloader.paths import (
    choose_destination,
    resource_directory,
    safe_component,
)


@pytest.mark.parametrize("name", ["CON.pdf", "NUL.txt", "COM1", "lpt9.dat", "CON. "])
def test_reserved_names(name):
    assert safe_component(name).startswith("_")


def test_component_removes_windows_invalid_characters():
    value = safe_component('  a<>:"/\\|?*\x00\n. ')
    assert not any(char in value for char in '<>:"/\\|?*\x00\n')
    assert not value.endswith((".", " "))
    assert len(safe_component("a" * 500)) == 80
    assert safe_component("..") == "untitled"


def test_destination_never_selects_existing_file(tmp_path):
    digest = "a" * 64
    first = choose_destination(tmp_path, "lecture.pdf", 10, digest)
    first.write_bytes(b"user file")
    second = choose_destination(tmp_path, "lecture.pdf", 10, digest)
    other_resource = choose_destination(tmp_path, "lecture.pdf", 11, digest)
    assert len({first, second, other_resource}) == 3
    assert first.read_bytes() == b"user file"
    assert first.suffix == ".pdf"
    assert "cmid10" in first.name


def test_directory_is_account_scoped_and_bounded():
    account = Account("student")
    course = Course(123, "long name " * 100, "")
    resource = Resource(123, 1, "", "../Week/" * 100, "")
    directory = resource_directory(Path("C:/Materials"), account, course, resource)
    assert directory.parts[-3] == account.slug
    assert directory.parts[-2].endswith("__123")
    assert len(str(directory)) < 155
    destination = choose_destination(directory, "a" * 500 + ".pdf", 1, "a" * 64)
    assert len(str(destination)) < 240
    assert destination.suffix == ".pdf"


def test_long_root_rejected():
    with pytest.raises(ValueError, match="too long"):
        resource_directory(Path("C:/" + "x" * 180), Account("student"), Course(1, "A", ""), Resource(1, 2, "", "", ""))
