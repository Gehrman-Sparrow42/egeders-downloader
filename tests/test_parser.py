from egeders_downloader.parser import (
    infer_resource_type,
    is_authenticated,
    parse_course,
    parse_courses,
)


def test_courses_official_positive_ids_deduplication():
    courses = parse_courses('''
      <a href="/course/view.php?id=178423">Economics 2627G</a>
      <a href="https://egeders.ege.edu.tr/course/view.php?id=178423">Economics 2627G</a>
      <a href="https://evil.example/course/view.php?id=10">External</a>
      <a href="https://egeders.ege.edu.tr.evil.example/course/view.php?id=11">Fake host</a>
      <a href="/course/view.php?id=-2">Negative</a><a href="/course/view.php?id=0">Zero</a>
      <a href="/course/view.php?id=3&id=4">Ambiguous</a>
      <a href="/course/view.php?id=3&id=">Blank duplicate</a>
      <a href="https://[invalid/course/view.php?id=5">Malformed</a>
      <a href="/course/view.php?id=4">Lab 2526B</a>
    ''')
    assert [(c.id, c.semester) for c in courses] == [(178423, "2627G"), (4, "2526B")]


def test_resources_sections_metadata_and_unsupported_deduplication():
    page = parse_course('''<ul>
      <li id="section-1" class="section"><h3 class="sectionname">Week 1</h3>
        <li class="activity"><div class="activityinstance">
          <a class="aalink" href="/mod/resource/view.php?id=123">Introduction <span class="accesshide">Kaynak</span></a>
          <a href="/mod/resource/view.php?id=123">Introduction</a>
        </div><div class="resourcedetails">573 KB PDF 19 October</div></li>
        <a href="/mod/quiz/view.php?id=15">Quiz</a><a href="/mod/quiz/view.php?id=15">Quiz again</a>
        <a href="/mod/folder/view.php?id=20">Folder</a>
        <a href="https://evil.example/mod/resource/view.php?id=99">Bad</a>
        <button data-action="toggle-manual-completion">Mark done</button>
      </li></ul>''', 178423)
    assert len(page.resources) == 1
    resource = page.resources[0]
    assert resource.id == 123
    assert resource.title == "Introduction"
    assert resource.section == "Week 1"
    assert "573 KB" in resource.metadata
    assert page.unsupported == {"quiz": 1, "folder": 1}


def test_inventory_activities_types_canonical_urls_security_and_deduplication():
    page = parse_course('''<ul>
      <li id="section-1" class="section"><h3 class="sectionname">Week 1</h3>
        <li class="activity"><a href="/mod/resource/view.php?id=101">Lecture 1.pptx</a></li>
        <li class="activity"><a href="/mod/resource/view.php?id=102">Legacy Slides.ppt</a></li>
        <li class="activity">
          <a href="/mod/resource/view.php?id=103"><span class="accesshide">Kaynak</span></a>
          <a href="/mod/resource/view.php?id=103">Reading Notes</a>
          <div class="resourcedetails">1.1 MB PDF</div>
        </li>
        <li class="activity"><a href="/mod/resource/view.php?id=104">Dataset.zip</a></li>
        <li class="activity"><a href="/mod/folder/view.php?id=201">Reference Folder</a></li>
        <li class="activity"><a href="/mod/url/view.php?id=202">External Reading</a></li>
        <li class="activity"><a href="/mod/page/view.php?id=203">Syllabus Page</a></li>
        <li class="activity"><a href="/mod/quiz/view.php?id=204">Week 1 Quiz</a><a href="/mod/quiz/view.php?id=204">Duplicate Quiz</a></li>
        <li class="activity"><a href="/mod/assign/view.php?id=205">Homework 1</a></li>
        <li class="activity"><a href="/mod/forum/view.php?id=206">Announcements</a></li>
        <a href="https://evil.example/mod/forum/view.php?id=900">Phishing Forum</a>
        <a href="https://egeders.ege.edu.tr.evil.example/mod/url/view.php?id=901">Spoofed</a>
        <a href="http://egeders.ege.edu.tr/mod/resource/view.php?id=902">Insecure HTTP</a>
        <a href="/mod/quiz/attempt.php?id=204">Quiz Attempt</a>
        <a href="/mod/assign/view.php?id=0">Zero ID</a>
        <a href="/mod/assign/view.php?id=-5">Negative ID</a>
        <a href="/mod/assign/view.php?id=1&id=2">Ambiguous ID</a>
      </li></ul>''', 178423)
    assert [(a.id, a.kind, a.type_label, a.title, a.downloadable) for a in page.activities] == [
        (101, "resource", "File (PPTX)", "Lecture 1.pptx", True),
        (102, "resource", "File (PPT)", "Legacy Slides.ppt", True),
        (103, "resource", "File (PDF)", "Reading Notes", True),
        (104, "resource", "File (ZIP)", "Dataset.zip", True),
        (201, "folder", "Folder", "Reference Folder", False),
        (202, "url", "URL", "External Reading", False),
        (203, "page", "Page", "Syllabus Page", False),
        (204, "quiz", "Quiz", "Week 1 Quiz", False),
        (205, "assign", "Assignment", "Homework 1", False),
        (206, "forum", "Forum", "Announcements", False),
    ]
    for item in page.activities:
        assert item.section == "Week 1"
        assert item.url == f"https://egeders.ege.edu.tr/mod/{item.kind}/view.php?id={item.id}"
        if item.downloadable:
            assert "Downloadable" in item.status
        else:
            assert "not downloaded or followed" in item.status
    assert infer_resource_type("Report.docx") == "File (DOCX)"
    assert infer_resource_type("Grades.xlsx") == "File (XLSX)"
    assert infer_resource_type("Deck", "2.0 MB PowerPoint presentation") == "File (PowerPoint)"


def test_authentication_requires_user_menu_and_rejects_guest():
    assert is_authenticated('<body><div class="usermenu"><a id="user-menu-toggle">Student</a></div><a href="/login/index.php">unrelated login link</a></body>')
    assert not is_authenticated('<a id="user-menu-toggle">Guest user</a>')
    assert not is_authenticated('<form id="login"><input type="password"></form><a id="user-menu-toggle">Student</a>')
    assert not is_authenticated('<body class="notloggedin"><a id="user-menu-toggle">Student</a></body>')
    assert not is_authenticated('<p>Welcome</p>')


def test_section_heading_outside_nested_moodle_activity_list():
    page = parse_course('''
      <ul class="topics">
        <li id="section-1" class="section course-section main clearfix" data-for="section">
          <div class="section-header" data-for="section_title">
            <span class="accesshide">Konu seçin Introduction</span>
            <h3 class="sectionname">Introduction</h3>
            <span class="accesshide">Introduction</span>
          </div>
          <div class="content"><ul class="section m-0 p-0 img-text d-block" data-for="cmlist">
            <li class="activity resource"><a href="/mod/resource/view.php?id=11">First PDF</a></li>
          </ul></div>
        </li>
        <li id="section-2" class="section course-section main clearfix" data-for="section">
          <h3 class="sectionname">Week Two</h3>
          <ul class="section" data-for="cmlist">
            <li class="activity"><a href="/mod/resource/view.php?id=12">Second PDF</a></li>
          </ul>
        </li>
      </ul>
    ''', 178423)
    assert [(resource.id, resource.section) for resource in page.resources] == [
        (11, "Introduction"), (12, "Week Two")
    ]
