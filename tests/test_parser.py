from egeders_downloader.parser import is_authenticated, parse_course, parse_courses


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
