"""Account-scoped SQLite download history, with per-operation connections."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .models import Course, Download, Resource


class Manifest:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS courses (
                    id INTEGER PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL, semester TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource_id INTEGER NOT NULL, course_id INTEGER NOT NULL,
                    title TEXT NOT NULL, section TEXT NOT NULL, resource_url TEXT NOT NULL,
                    metadata TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
                    path TEXT NOT NULL, filename TEXT NOT NULL, url TEXT NOT NULL,
                    content_type TEXT NOT NULL, downloaded_at TEXT NOT NULL, checked_at TEXT NOT NULL,
                    UNIQUE(resource_id, sha256, path)
                );
                CREATE INDEX IF NOT EXISTS versions_resource ON versions(resource_id, id);
                CREATE TABLE IF NOT EXISTS conversions (
                    resource_id INTEGER NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    pdf_path TEXT NOT NULL,
                    pdf_sha256 TEXT NOT NULL,
                    pdf_size INTEGER NOT NULL,
                    converter TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (resource_id, source_sha256, source_path)
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def latest(self, resource_id: int) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM versions WHERE resource_id=? ORDER BY checked_at DESC, id DESC LIMIT 1", (resource_id,)).fetchone()
            return dict(row) if row else None

    def versions(self, resource_id: int) -> list[dict]:
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM versions WHERE resource_id=? ORDER BY id", (resource_id,))]

    def record(self, course: Course, resource: Resource, download: Download, path: Path) -> None:
        if resource.course_id != course.id or resource.id <= 0 or course.id <= 0:
            raise ValueError("Resource and course identities must match.")
        timestamp = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute("""INSERT INTO courses VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url,semester=excluded.semester""",
                (course.id, course.title, course.url, course.semester))
            connection.execute("""INSERT INTO versions
                (resource_id,course_id,title,section,resource_url,metadata,sha256,size,path,filename,url,content_type,downloaded_at,checked_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(resource_id,sha256,path) DO UPDATE SET
                    checked_at=excluded.checked_at,metadata=excluded.metadata,title=excluded.title,section=excluded.section""",
                (resource.id, course.id, resource.title, resource.section, resource.url, resource.metadata,
                 download.sha256, download.size, str(Path(path).resolve()), download.filename, download.url,
                 download.content_type, timestamp, timestamp))

    def conversion(self, resource_id: int, source_sha256: str, source_path: Path) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM conversions WHERE resource_id=? AND source_sha256=? AND source_path=?",
                (resource_id, source_sha256, str(Path(source_path).resolve())),
            ).fetchone()
            return dict(row) if row else None

    def record_conversion(
        self,
        resource_id: int,
        source_sha256: str,
        source_path: Path,
        *,
        status: str,
        converter: str = "",
        pdf_path: Path | None = None,
        pdf_sha256: str = "",
        pdf_size: int = 0,
        error: str = "",
    ) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        resolved_source = str(Path(source_path).resolve())
        resolved_pdf = str(Path(pdf_path).resolve()) if pdf_path is not None else ""
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO conversions
                (resource_id,source_sha256,source_path,pdf_path,pdf_sha256,pdf_size,converter,status,error,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(resource_id,source_sha256,source_path) DO UPDATE SET
                    pdf_path=excluded.pdf_path,pdf_sha256=excluded.pdf_sha256,pdf_size=excluded.pdf_size,
                    converter=excluded.converter,status=excluded.status,error=excluded.error,updated_at=excluded.updated_at""",
                (
                    resource_id,
                    source_sha256,
                    resolved_source,
                    resolved_pdf,
                    pdf_sha256,
                    pdf_size,
                    converter,
                    status,
                    error,
                    timestamp,
                ),
            )

    def status(self) -> list[dict]:
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute("""SELECT v.*, c.title AS course_title,
                    conv.pdf_path AS pdf_path, conv.status AS conversion_status, conv.error AS conversion_error
                FROM versions v JOIN courses c ON c.id=v.course_id
                LEFT JOIN conversions conv ON conv.resource_id=v.resource_id
                    AND conv.source_sha256=v.sha256 AND conv.source_path=v.path
                WHERE v.id=(SELECT v2.id FROM versions v2 WHERE v2.resource_id=v.resource_id
                    ORDER BY v2.checked_at DESC,v2.id DESC LIMIT 1)
                ORDER BY c.title,v.title""")]
