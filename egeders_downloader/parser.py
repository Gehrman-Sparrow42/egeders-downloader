"""Read-only parsers for the official Moodle course and resource links."""

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from bs4.element import Tag

from .models import MOODLE_ORIGIN, Course, CoursePage, Resource


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
        if kind != "resource":
            unsupported_ids.setdefault(kind, set()).add(identifier)
            continue
        if identifier in resources:
            continue
        activity = link.find_parent(lambda node: isinstance(node, Tag) and "activity" in node.get("class", []))
        metadata = ""
        if activity:
            details = activity.select(".activity-information, .resourcedetails, .resourcecontent, .activity-altcontent")
            metadata = " | ".join(detail.get_text(" ", strip=True) for detail in details)
        resources[identifier] = Resource(course_id, identifier, _title(link) or f"Resource {identifier}",
                                         _section_title(link), url, metadata)
    return CoursePage(list(resources.values()), {kind: len(ids) for kind, ids in unsupported_ids.items()})


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
