# Verification record — 2026-10-19

Implementation follows the Argon Medium plan saved at `C:/Tools/plans/egeders-downloader/argon-plan.md`, with hash verification on every sync and no password storage in the first version.

## Live checks

- The application's automatic SSO sign-in succeeded using the supplied account, with no credentials embedded in source or saved configuration.
- A new CLI invocation reused the local session and discovered 30 enrolled courses.
- Engineering Economics (course 178423) sync: 6 new, 0 updated, 0 unchanged, 0 failed. One forum activity was skipped.
- Immediate repeat: 0 new, 0 updated, 6 unchanged, 0 failed. No duplicate PDFs or partial files remained.
- Six PDFs parsed with pypdf: 33,516,334 total bytes and 121 pages. The Introduction file was 587,455 bytes and 8 pages.
- The final course parser recognized Introduction, Economical analysis, Fundamentals, Cost estimation - 1, Cost estimation - 2, and Interest and investment costs. Files saved during the initial run remain in their original General directory; historical paths are retained to avoid relocation or duplication.

## Local checks

- 53 pytest tests passed.
- Ruff checks passed for application modules and tests.
- Python compilation passed.
- Native Tkinter smoke check covered create/update/destroy, filtering, preserving hidden selections, saving and background-worker queue completion.
- Workspace JSON/TOML settings parsed successfully after adding the project import path.
- Shared-environment pip check reported no broken distribution requirements after installing Playwright.

## Practical limits

Moodle file-resource activities are supported. Folders, external URLs, forum/assignment attachments, quiz content, and other activity types are skipped. Automatic re-login after a university session expires, long-term session lifetimes, and scheduled execution under Windows Task Scheduler were not validated. Scheduling instructions are provided; no persistent task was registered. Downloads buffer in memory and reject files over 150 MiB.

At the implementation checkpoint, the project was a local directory without Git. Git was subsequently initialized on `main` for publication to the private `Gehrman-Sparrow42/egeders-downloader` repository. Local sessions, settings, databases, downloaded materials, and caches are excluded from version control.
