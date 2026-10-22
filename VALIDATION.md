# Verification record — 2026-10-19

## Presentation conversion and course inventory — 0.3.0 (2026-10-22)

- GUI and CLI inventory enumerate named file resources and unsupported Moodle activities with course, section, type, canonical URL and status, without following unsupported activities.
- Optional PPT/PPTX PDF conversion preserves originals, retries conversions after prior failure, and does not overwrite user-edited PDFs. Missing converters and conversion failures are separate from download failures.
- Conversion uses installed PowerPoint or LibreOffice; neither suite is bundled. PowerPoint macros are disabled before opening presentations, pre-existing Office sessions are not borrowed, and process cleanup requires an identified helper PID. Visible PowerPoint windows are preserved during cleanup.
- The integrated suite passed 72 tests, including real synthetic PPTX and PPT conversions, inventory parsing/security, conversion history/retry, unavailable backend behavior, preservation, and process-ownership regression tests. Ruff and Python compilation passed.
- Frozen desktop and CLI executables each converted a synthetic presentation (PPTX and PPT respectively), producing valid one-page PDFs containing the expected slide text. Input hashes stayed unchanged; no PowerPoint processes remained.
- Frozen doctor confirmed blank first-run credentials, browser startup, GUI initialization, and detection of the local PowerPoint converter.
- Version 0.3.0 installed successfully into an isolated test directory; its installed executable matched the tested build and passed doctor. Uninstall removed the application successfully. ZIP content checks and independent SHA-256 recomputation passed for both download packages.
- No university credentials or course session were reused for these checks. Real LibreOffice conversion was not tested because LibreOffice is not installed; its invocation and isolated profile were covered by unit tests.

## Windows distribution (0.2.0)

- New installation starts with blank username/password fields and no selected courses. Remembering a username requires an explicit selection.
- The development account settings and saved university session were removed; downloaded PDFs and the old history database were retained locally.
- Default state now lives in the Windows user's Local AppData directory, independently of the installed program. The old development `.local` state is not imported.
- 55 automated tests pass, including onboarding and selective sign-out. Ruff and syntax checks pass.
- The frozen application passed an offline check that creates the actual GUI, verifies blank first-run fields, and launches bundled Chromium 156 without account credentials or site requests.
- Windows x64 installer, portable ZIP, and SHA-256 checksums are built from explicit source/runtime inputs. Release content was checked to exclude settings, login sessions, and history databases.
- The installer was executed into an isolated test directory. Its installed CLI passed the same UI/browser/blank-account check. The uninstaller then completed successfully and removed the application executable. Both published download checksums were independently recomputed.

The live course test below belongs to the earlier implementation. The 0.2.0 packaging checks use no university account and do not repeat authenticated downloads.

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

At the implementation checkpoint, the project was a local directory without Git. Git was subsequently initialized on `main` for publication to `Gehrman-Sparrow42/egeders-downloader`, which was subsequently made public at the user's request. Local sessions, settings, databases, downloaded materials, and caches are excluded from version control.
