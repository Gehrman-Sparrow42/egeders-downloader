# Ege Ders Downloader

A local Windows application that signs in through EgeSSO, discovers your Ege Ders courses, and saves selected course resources. It includes a native desktop interface and a command line for repeat runs.

## Start

Double-click `C:\Tools\LAUNCHERS\Launch_EgeDers_Downloader.bat`.

From PowerShell:

```powershell
Set-Location C:\Tools\egeders_downloader
& C:\Tools\.venv\Scripts\python.exe -m egeders_downloader
```

The application uses the shared workspace Python environment. For a fresh setup:

```powershell
& C:\Tools\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Microsoft Edge is the default browser. It must be installed; separate Playwright browser downloads are not required for this configuration. Manual login opens a visible browser; password login can run in the background.

## First sync

1. Enter your university username and choose its domain.
2. Choose **Manual browser login** to sign in yourself, or enter a password and choose **Sign in with password**. The password field is cleared when submitted. Passwords are not saved by the application.
3. Choose **Discover courses**. Courses from multiple semesters may appear.
4. Filter by text or semester. Click the **Sync** column to check a course, or highlight rows and press Space. **Select visible** affects only the displayed rows. Hidden selections remain selected. Courses start unchecked unless you previously saved a selection for that account.
5. Choose an absolute output folder and **Save selection**. Choose **Sync selected courses** to start.

The default output root is `Documents\Ege Ders Materials` under your Windows user profile. Downloads are isolated by account and grouped by course and section. **Cancel sync** asks the current sync to stop after the current operation; an in-flight network request may take time to finish.

## Supported materials and preservation

This first version supports Moodle **resource** activities (`/mod/resource/view.php`) that resolve to downloadable Moodle files. PDF files are validated as PDF data. Folder activities, external URL activities, forum attachments, quizzes, and assignment attachments are outside this version's scope. Unsupported activities are counted rather than followed.

Sync re-downloads resource bytes and compares SHA-256 hashes by default. This detects changed contents even when an instructor keeps the same filename, displayed date, or file size. Identical bytes do not create a duplicate file. Changed bytes are saved as a new version, preserving older files. Existing user files are preserved. Materials that disappear from a course are retained locally. The application never removes downloaded course files.

This verification costs additional download traffic on repeated syncs. There is no metadata-only fast-skip mode in this version.

Downloads are sequential, with request timeouts and bounded retries. Files over 150 MiB are rejected. Playwright buffers HTTP responses in memory, so this is a file-acceptance limit rather than a strict streaming memory limit. Very large video files are outside the intended use of this version.

## Command line

Run these examples from `C:\Tools\egeders_downloader`. Global options such as `--account` and `--state-dir` go **before** the subcommand. Substitute your account and course IDs.

```powershell
# Sign in yourself in the browser; saves the session without saving a password
& C:\Tools\.venv\Scripts\python.exe -m egeders_downloader.cli --account USER@ogrenci.ege.edu.tr login --manual

# Or prompt for a masked password in an interactive terminal
& C:\Tools\.venv\Scripts\python.exe -m egeders_downloader.cli --account USER@ogrenci.ege.edu.tr login

# List courses and their IDs
& C:\Tools\.venv\Scripts\python.exe -m egeders_downloader.cli courses

# Sync explicitly selected courses to an absolute output root
& C:\Tools\.venv\Scripts\python.exe -m egeders_downloader.cli sync --course 12345 67890 --output 'C:\Users\YOURNAME\Documents\Ege Ders Materials'

# Reuse the account, output and course selection saved in the GUI
& C:\Tools\.venv\Scripts\python.exe -m egeders_downloader.cli sync

# Machine-readable summary or recorded local resource status
& C:\Tools\.venv\Scripts\python.exe -m egeders_downloader.cli sync --json
& C:\Tools\.venv\Scripts\python.exe -m egeders_downloader.cli status --json

# Custom state location
& C:\Tools\.venv\Scripts\python.exe -m egeders_downloader.cli --state-dir 'C:\Private\EgeDersState' --account USER@ogrenci.ege.edu.tr courses
```

`--course` accepts multiple IDs and may be repeated. Explicit CLI course IDs apply to that run; use the GUI to persist a selection. CLI exit codes are `0` for success, `1` for errors or failed/cancelled syncs, `2` for expired authentication, and `130` for an interrupted command. JSON summaries go to stdout; progress goes to stderr.

## Optional scheduled sync

No Windows scheduled task is installed automatically. Run:

```powershell
& C:\Tools\.venv\Scripts\python.exe -m egeders_downloader.cli schedule-help
```

In Task Scheduler, create a task under the same Windows user that saved the session:

| Field | Value |
| --- | --- |
| Program | `C:\Tools\.venv\Scripts\python.exe` |
| Arguments | `-m egeders_downloader.cli --account USER@ogrenci.ege.edu.tr sync` |
| Start in | `C:\Tools\egeders_downloader` |
| Overlap setting | Do not start a new instance |

Save a course selection and output folder in the GUI first. You can instead pass explicit `--course` IDs and `--output` after `sync`. For a custom state directory, add `--state-dir C:\absolute\state` before `sync`.

Saved university sessions expire on a schedule controlled by the university. An unattended sync reports expired authentication and exits with code 2; it does not prompt invisibly or hang waiting for a password. Re-run `login --manual` interactively and retry the sync. Automatic password storage and automatic re-login after expiry are not included.

## Local data

Settings, authentication sessions, and SQLite history live in the project's `.local` directory by default. Use global `--state-dir` for a separate CLI state root. Accounts have separate sessions and manifest history. Authentication state grants access to your university session; keep `.local` private. It is excluded by `.gitignore`, along with database files, partial downloads, caches, and environment files.

The source contains no account details or saved passwords. This tool reads course content and download resources; it does not submit assignments, quizzes, completion toggles, or messages. Manual login actions remain under your control in the browser.

## Verification

Tests use local fixtures and temporary files:

```powershell
& C:\Tools\.venv\Scripts\python.exe -m pytest
```

Live account tests require a real session. Successful login and one course download do not establish support for every Moodle activity or long-term session behavior.

Verified on 2026-10-19: real SSO login, saved-session reuse, discovery of 30 courses, six Engineering Economics PDFs downloaded without failure, and an immediate second sync reporting six unchanged resources with no duplicates. All six saved PDFs parsed successfully (33,516,334 total bytes, 121 pages). The final live parser check confirmed six distinct section headings. The automated suite passed 53 tests, including SAML navigation races, changed-file preservation, user edits, failed downloads, account locks, and recovery after a manifest write fails. Ruff and syntax checks passed; Tkinter initialization, filtering, selection persistence and worker completion were smoke-tested. No scheduled task was installed.
