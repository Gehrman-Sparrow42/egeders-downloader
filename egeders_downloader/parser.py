"""Read-only parsers for the official Moodle course and resource links."""

import re
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from bs4.element import Tag

from .models import MOODLE_ORIGIN, Activity, Course, CoursePage, Resource

_UNSUPPORTED_LABELS = {
    "assign": "Assignment",
    "assignment": "Assignment",
    "bigbluebuttonbn": "BigBlueButton",
    "book": "Book",
    "chat": "Chat",
    "choice": "Choice",
    "data": "Database",
    "feedback": "Feedback",
    "folder": "Folder",
    "forum": "Forum",
    "glossary": "Glossary",
    "label": "Label",
    "lesson": "Lesson",
    "lti": "External tool",
    "page": "Page",
    "quiz": "Quiz",
    "scorm": "SCORM",
    "survey": "Survey",
    "url": "URL",
    "wiki": "Wiki",
    "workshop": "Workshop",
}

_EXTENSION_TYPES = {
    ".pdf": "File (PDF)",
    ".pptx": "File (PPTX)",
    ".ppt": "File (PPT)",
    ".docx": "File (DOCX)",
    ".doc": "File (DOC)",
    ".xlsx": "File (XLSX)",
    ".xls": "File (XLS)",
    ".zip": "File (ZIP)",
    ".rar": "File (RAR)",
    ".7z": "File (7Z)",
    ".csv": "File (CSV)",
    ".txt": "File (TXT)",
    ".rtf": "File (RTF)",
    ".odt": "File (ODT)",
    ".ods": "File (ODS)",
    ".odp": "File (ODP)",
    ".png": "File (Image)",
    ".jpg": "File (Image)",
    ".jpeg": "File (Image)",
    ".gif": "File (Image)",
    ".webp": "File (Image)",
    ".svg": "File (Image)",
    ".mp4": "File (Video)",
    ".mkv": "File (Video)",
    ".avi": "File (Video)",
    ".mov": "File (Video)",
    ".mp3": "File (Audio)",
    ".wav": "File (Audio)",
    ".ogg": "File (Audio)",
}


def infer_resource_type(title: str, metadata: str = "", hint_text: str = "") -> str:
    """Classify a downloadable Moodle resource from its title, details or icon hints."""
    for candidate in (title.strip(), metadata.strip()):
        ext = Path(candidate).suffix.lower()
        if ext in _EXTENSION_TYPES:
            return _EXTENSION_TYPES[ext]
        if re.fullmatch(r"\.[a-z0-9]{2,5}", ext) and candidate == title.strip():
            return f"File ({ext[1:].upper()})"
    combined = f"{title} {metadata} {hint_text}".casefold()
    if re.search(r"\bpptx\b|powerpoint\s*2007|presentationml", combined):
        return "File (PPTX)"
    if re.search(r"\bppt\b", combined):
        return "File (PPT)"
    if re.search(r"powerpoint|/f/powerpoint|\bsunum\b|\bpresentation\b", combined):
        return "File (PowerPoint)"
    if re.search(r"\bpdf\b|/f/pdf", combined):
        return "File (PDF)"
    if re.search(r"\bdocx\b|word\s*2007|wordprocessingml", combined):
        return "File (DOCX)"
    if re.search(r"\bdoc\b|\bword\b|/f/document", combined):
        return "File (DOC)"
    if re.search(r"\bxlsx\b|spreadsheetml", combined):
        return "File (XLSX)"
    if re.search(r"\bxls\b|\bexcel\b|\bspreadsheet\b|/f/spreadsheet", combined):
        return "File (XLS)"
    if re.search(r"\bzip\b", combined):
        return "File (ZIP)"
    if re.search(r"\brar\b", combined):
        return "File (RAR)"
    if re.search(r"\b7z\b|\barchive\b|/f/archive", combined):
        return "File (Archive)"
    if re.search(r"\bcsv\b", combined):
        return "File (CSV)"
    if re.search(r"\btxt\b|\btext\b|/f/text", combined):
        return "File (TXT)"
    if re.search(r"\b(png|jpe?g|gif|webp|image)\b|/f/(image|jpeg|png|gif)", combined):
        return "File (Image)"
    if re.search(r"\b(mp4|mkv|avi|mov|video)\b|/f/(video|mpeg|avi)", combined):
        return "File (Video)"
    if re.search(r"\b(mp3|wav|ogg|audio)\b|/f/(audio|mp3|wav)", combined):
        return "File (Audio)"
    return "File"


def _official_id(href: str, path: str) -> tuple[int, str] | None:
    try:
        url = urljoin(MOODLE_ORIGIN + "/", href)
        parsed = urlsplit(url)
    except ValueError:
        return None
    if (parsed.scheme, parsed.netloc) != ("https", "egeders.ege.edu.tr") or parsed.path != path:
        return None
    values = parse_qs(parsed.query, keep_blank_values=True).get("id", [])
    if len(values) != 1 or not re.fullmatch(r"[0-9]+", values[0]) or int(values[0]) <= 0:
        return None
    identifier = int(values[0])
    return identifier, f"{MOODLE_ORIGIN}{path}?id={identifier}"


def _title(element: Tag) -> str:
    # Moodle adds resource-type accessibility labels within the anchor.
    clone = BeautifulSoup(str(element), "html.parser")
    for extra in clone.select(".accesshide, .sr-only, .visually-hidden"):
        extra.decompose()
    return clone.get_text(" ", strip=True)


def parse_courses(html: str) -> list[Course]:
    soup = BeautifulSoup(html, "html.parser")
    courses: dict[int, Course] = {}
    for link in soup.select("a[href]"):
        match = _official_id(str(link.get("href", "")), "/course/view.php")
        if not match:
            continue
        identifier, url = match
        title = _title(link) or str(link.get("title", "")).strip()
        if not title:
            continue
        semester = re.search(r"\b(\d{4}[GBY])\b", title, re.IGNORECASE)
        course = Course(identifier, title, url, semester.group(1).upper() if semester else "")
        previous = courses.get(identifier)
        if previous is None or len(title) > len(previous.title):
            courses[identifier] = course
    return list(courses.values())


def _section_title(link: Tag) -> str:
    for section in link.parents:
        if not isinstance(section, Tag):
            continue
        # Moodle uses .section both for the course section and its inner
        # activity list. Only the outer container owns the section heading.
        if section.get("data-for") == "cmlist" or section.name in {"ul", "ol"}:
            continue
        is_section = (
            re.fullmatch(r"section-\d+", str(section.get("id", "")))
            or section.get("data-for") == "section"
            or "section" in section.get("class", [])
        )
        if is_section:
            # Selector lists follow DOM order, not selector priority. Moodle's
            # outer section_title wrapper also includes hidden menu labels.
            heading = (
                section.select_one(".sectionname")
                or section.select_one("h2, h3")
                or section.select_one("[data-for='section_title']")
            )
            if heading:
                title = heading.get_text(" ", strip=True)
                if title:
                    return title
    return "General"


def parse_course(html: str, course_id: int) -> CoursePage:
    if course_id <= 0:
        raise ValueError("Course ID must be positive.")
    soup = BeautifulSoup(html, "html.parser")
    resources: dict[int, Resource] = {}
    unsupported_ids: dict[str, set[int]] = {}
    activities: dict[tuple[str, int], Activity] = {}
    titled_keys: set[tuple[str, int]] = set()
    for link in soup.select("a[href]"):
        href = str(link.get("href", ""))
        try:
            parsed = urlsplit(urljoin(MOODLE_ORIGIN + "/", href))
        except ValueError:
            continue
        module = re.fullmatch(r"/mod/([a-z][a-z0-9_]*)/view\.php", parsed.path)
        if not module:
            continue
        match = _official_id(href, parsed.path)
        if not match:
            continue
        identifier, url = match
        kind = module.group(1)
        key = (kind, identifier)
        raw_title = _title(link) or str(link.get("title", "")).strip()
        if key != ("resource", identifier):
            unsupported_ids.setdefault(kind, set()).add(identifier)
        if key in activities and (key in titled_keys or not raw_title):
            continue
        activity = link.find_parent(lambda node: isinstance(node, Tag) and "activity" in node.get("class", []))
        metadata = ""
        hints = [node.get_text(" ", strip=True) for node in link.select(".accesshide, .sr-only, .visually-hidden")]
        if activity:
            details = activity.select(".activity-information, .resourcedetails, .resourcecontent, .activity-altcontent")
            metadata = " | ".join(detail.get_text(" ", strip=True) for detail in details)
            for img in activity.select("img"):
                hints.extend((str(img.get("src", "")), str(img.get("alt", ""))))
        else:
            for img in link.select("img"):
                hints.extend((str(img.get("src", "")), str(img.get("alt", ""))))
        section = _section_title(link)
        if raw_title:
            titled_keys.add(key)
        if kind == "resource":
            title = raw_title or f"Resource {identifier}"
            type_label = infer_resource_type(title, metadata, " ".join(hints))
            status = (
                "Downloadable file resource (PPT/PPTX → PDF supported)"
                if type_label in {"File (PPTX)", "File (PPT)", "File (PowerPoint)"}
                else "Downloadable file resource"
            )
            resources[identifier] = Resource(course_id, identifier, title, section, url, metadata)
            activities[key] = Activity(
                course_id, identifier, title, section, kind, type_label, url, True, status, metadata
            )
        else:
            type_label = _UNSUPPORTED_LABELS.get(kind, kind.replace("_", " ").title())
            title = raw_title or f"{type_label} {identifier}"
            status = f"Unsupported {type_label.lower()} (not downloaded or followed)"
            activities[key] = Activity(
                course_id, identifier, title, section, kind, type_label, url, False, status, metadata
            )
    return CoursePage(
        list(resources.values()),
        {kind: len(ids) for kind, ids in unsupported_ids.items()},
        list(activities.values()),
    )


def is_authenticated(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("form#login, form.loginform, input[type='password']"):
        return False
    if soup.body is not None and "notloggedin" in soup.body.get("class", []):
        return False
    menu = soup.select_one("#user-menu-toggle")
    if menu is None:
        return False
    user = menu.find_parent(class_="usermenu") or menu
    text = user.get_text(" ", strip=True).casefold()
    return not any(word in text for word in ("guest", "misafir", "giriş yap", "log in"))
