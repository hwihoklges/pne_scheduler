"""PNE Scheduler web API launcher (localhost only).

The bind is 127.0.0.1. Local mode additionally validates Host, Origin and peer;
PNE_LOCAL_RESOURCES=0 disables local filesystem routes. PNE_SERVER_MODE=cloud
always disables them and requires PNE_API_TOKEN and PNE_ALLOWED_HOSTS (see
api.security). Shared hosting needs a separate TLS/auth gateway, not this
convenience launcher. Tokens must never be sent to browser storage.
"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
if str(_root.parent) not in sys.path:
    sys.path.insert(0, str(_root.parent))

from pne_scheduler.api.app import main


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    raise SystemExit(main(port=port))
