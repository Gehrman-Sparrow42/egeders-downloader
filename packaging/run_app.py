"""Frozen entry point, shared by the GUI and console executables."""

import sys

if len(sys.argv) > 1:
    from egeders_downloader.cli import main
else:
    from egeders_downloader.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
