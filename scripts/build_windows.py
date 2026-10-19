"""Produce an account-free Windows ZIP, installer, and SHA-256 checksums."""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH="0")
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], env=env, check=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    str(ROOT / "packaging" / "windows.spec")], cwd=ROOT, env=env, check=True)
    bundle = ROOT / "dist" / "EgeDersDownloader"
    subprocess.run([str(bundle / "EgeDersCLI.exe"), "doctor"], check=True, cwd=bundle)
    # Only the explicit frozen output enters the archive, never the checkout.
    forbidden = {".local", "settings.json", "storage_state.json", "session_account.json"}
    for path in bundle.rglob("*"):
        if path.name in forbidden or path.suffix in {".sqlite3", ".db", ".part"}:
            raise RuntimeError(f"Runtime data must not enter a release: {path.name}")
    shutil.copy2(ROOT / "README.md", bundle / "README.md")
    archive = shutil.make_archive(str(ROOT / "dist" / f"EgeDersDownloader-{version}-Windows-x64"),
                                  "zip", root_dir=bundle.parent, base_dir=bundle.name)
    candidates = [shutil.which("ISCC.exe"),
                  str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe"),
                  r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"]
    compiler = next((path for path in candidates if path and Path(path).is_file()), None)
    if not compiler:
        raise RuntimeError("Install Inno Setup 6 to build the Windows installer.")
    subprocess.run([compiler, f"/DAppVersion={version}", str(ROOT / "packaging/installer.iss")], check=True)
    installer = ROOT / "dist" / f"EgeDersDownloader-{version}-Setup-x64.exe"
    artifacts = [Path(archive), installer]
    lines = []
    for path in artifacts:
        with path.open("rb") as stream:
            lines.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {path.name}")
    (ROOT / "dist/SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Release ready:", *(str(path) for path in artifacts), sep="\n")


if __name__ == "__main__":
    main()
