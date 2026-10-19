# Build only explicit application modules and Playwright's runtime; never local state.
from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis([str(root / "packaging" / "run_app.py")], pathex=[str(root)],
             hiddenimports=["egeders_downloader.cli", "egeders_downloader.gui"],
             excludes=["IPython", "matplotlib", "numpy", "pandas", "pytest"],
             datas=[], binaries=[])
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EgeDersDownloader",
          console=False, debug=False, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EgeDersCLI",
          console=True, debug=False, upx=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="EgeDersDownloader", upx=False)
