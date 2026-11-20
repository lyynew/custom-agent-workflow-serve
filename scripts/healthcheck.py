"""Container healthcheck.

`/health` verifies PostgreSQL, the checkpointer and the store, and answers 503
when any of them is down — which is exactly the readiness signal Docker needs.
Deliberately dependency-free: no curl in the slim image.
"""

import sys
import urllib.error
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:2026/health"

try:
    with urllib.request.urlopen(URL, timeout=5) as response:
        sys.exit(0 if response.status == 200 else 1)
except (urllib.error.URLError, OSError):
    sys.exit(1)
