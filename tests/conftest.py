"""Put the repo's ``tools/`` directory on ``sys.path`` so tests can
import the dev-only helpers there (e.g. ``fake_xtream_panel`` for the
Xtream series integration test). ``tools/`` is not part of the installed
package on purpose.
"""

import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
