#!/usr/bin/env python3
"""Tune live-proxy stream switching in /app/apps/proxy/config.py.

Only ONE knob here is real. Verified against the running code 2026-08-08:

  MAX_RETRIES (default 3) is the number of attempts made against the SAME URL
  before _try_next_stream() rotates to an alternate. Read via
  ConfigHelper.max_retries() -> getattr(TSConfig, 'MAX_RETRIES', 3) and consumed
  by live_proxy/input/manager.py:50. Backoff between attempts is min(.25*n, 3),
  so a fast-failing URL (HTTP 503) burns ~4s for all three, but a URL that
  TIMES OUT burns CONNECTION_TIMEOUT (10s) per attempt, i.e. ~30s of Plex
  spinner before the first alternate is even tried. The probe found the UHD
  dead bucket is timeout-dominated (25.7% dead vs 13.2% HD/SD), so that path
  is real. 3 -> 2 halves the worst case while still absorbing one transient
  blip on an otherwise-healthy stream.

Knobs that LOOK tunable here but are NOT - do not re-add them:
  * MAX_RECONNECT_ATTEMPTS - dead code. Nothing in /app reads it (grep across
    the whole tree returns only its own definition). Patching it does nothing.
  * BUFFERING_TIMEOUT - the class attribute is shadowed by an @property that
    calls get_proxy_settings(), which reads the CoreSettings 'proxy_settings'
    JSON row. The live value there is already 45 (not the 15 in this file).
    Change it in the UI/DB, never here.

Deliberately unchanged:
  STREAM_TIMEOUT(20) + FAILOVER_GRACE_PERIOD(20) = 40s must stay under
  GHOST_CLIENT_MULTIPLIER(10) * CLIENT_HEARTBEAT_INTERVAL(5) = 50s, per the
  upstream comment. MAX_STREAM_SWITCHES stays at 10 - it only costs time when
  everything is already failing, and lowering it risks giving up on a channel
  that would have connected on a later alternate.

Idempotent. Prints "patched" if it changed anything, else "already".
"""
import re

PATH = "/app/apps/proxy/config.py"

EDITS = [
    # (regex, replacement, human label)
    (r"^(\s*)MAX_RETRIES\s*=\s*\d+",
     r"\g<1>MAX_RETRIES = 2",
     "MAX_RETRIES -> 2"),
    # Leave the dead constant at its upstream default so nothing here looks
    # load-bearing when the next person reads the file.
    (r"^(\s*)MAX_RECONNECT_ATTEMPTS\s*=\s*\d+",
     r"\g<1>MAX_RECONNECT_ATTEMPTS = 3",
     "MAX_RECONNECT_ATTEMPTS -> 3 (upstream default; constant is unused)"),
]

src = open(PATH).read()
orig = src

for pattern, repl, label in EDITS:
    new, n = re.subn(pattern, repl, src, count=1, flags=re.M)
    if n != 1:
        raise SystemExit("FAILED: pattern matched %d times (expected 1): %s" % (n, label))
    src = new

if src == orig:
    print("already")
else:
    open(PATH, "w").write(src)
    print("patched")
