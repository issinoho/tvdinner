"""PyInstaller entry point for the Windows build.

PyInstaller needs a real, analyzable .py script -- not the
`console_scripts` entry point tvdinner installs normally -- so this
just calls straight into the same tvdinner.cli.main() everything else
uses.
"""

import sys

from tvdinner.cli import main

if __name__ == "__main__":
    sys.exit(main())
