"""Shared, credential-free contracts for browser, storage and interface modules."""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

MOODLE_ORIGIN = "https://egeders.ege.edu.tr"
SSO_URL = "https://kimlik.ege.edu.tr/Identity/Account/Login"


class AuthExpiredError(RuntimeError):
    """The user must sign in again."""


class InvalidContentError(RuntimeError):
    """A resource did not return a valid downloadable file."""


@dataclass(frozen=True)
class Account:
    username: str
    domain: str = "ogrenci.ege.edu.tr"

    def __post_init__(self):
        username = self.username.strip().lower()
        domain = self.domain.strip().lower()
        if "@" in username:
            username, domain = username.split("@", 1)
        if not re.fullmatch(r"[a-z0-9._-]+", username):
            raise ValueError("Enter a valid university username.")
        if domain not in {"ogrenci.ege.edu.tr", "ege.edu.tr", "mail.ege.edu.tr"}:
            raise ValueError("Choose an Ege university account domain.")
        object.__setattr__(self, "username", username)
        object.__setattr__(self, "domain", domain)

    @property
    def key(self) -> str:
        return f"{self.username}@{self.domain}"

    @property
    def slug(self) -> str:
        return hashlib.sha256(self.key.encode()).hexdigest()[:20]


@dataclass(frozen=True)
class Course:
    id: int
    title: str
    url: str
    semester: str = ""


@dataclass(frozen=True)
class Resource:
    course_id: int
    id: int
    title: str
    section: str
    url: str
    metadata: str = ""


@dataclass
class CoursePage:
    resources: list[Resource]
    unsupported: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Download:
    temp_path: Path
    filename: str
    sha256: str
    size: int
    url: str
    content_type: str


@dataclass
class Summary:
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    unsupported: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)
