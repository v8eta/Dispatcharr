#!/usr/bin/env python3
"""
Idempotent patch: raise the EPG/M3U chunk-cache TTL so rebuilds are rare.

`/output/epg` XMLTV regeneration builds XML for ~65k programs (CPU-bound, >60s). The
single-flight Redis chunk cache (apps/output/streaming_chunk_cache.py) rebuilds on
every cache MISS, and upstream hardcodes `DEFAULT_CACHE_TTL = 300` -> a full rebuild
~12x/hour. EPG data only changes when sources refresh (hours), so a longer cache is
safe for Plex/clients. Made worse by a Redis flush on every container restart (cold
cache after each update).

One change to /app/apps/output/streaming_chunk_cache.py, marker-guarded by
`touki-epg-hubfreeze`: `DEFAULT_CACHE_TTL` 300 -> env DISPATCHARR_OUTPUT_CACHE_TTL
(default 3600s), a full rebuild at most ~1x/hour.

RETIRED 2026-08-27 - the cooperative-yield half. This patch also injected
`gevent.sleep(0)` after each chunk in `_stream_build`, because a rebuild with no yield
froze the uWSGI gevent worker hub and hung every co-located greenlet (login/API/HDHR).
Upstream has since added `core.utils._cooperative_yield()` - the same
`_is_gevent_monkey_patched()` guard plus `gevent.sleep(0)` - and calls it in
`_stream_build` on the line immediately before `yield chunk`, which is exactly where
this patch injected. Keeping it would only add a duplicate sleep one line later.

Applied every saltbox_mod run (the dev image is pulled; baked files reset on each pull).

Run:  docker exec dispatcharr python3 /tmp/patch-epg-hubfreeze.py
Test: python3 patch-epg-hubfreeze.py <copy-of-streaming_chunk_cache.py>
Exit: 0 = ok (applied and/or already present), 1 = an anchor was not found.
After a change, restart the container (docker restart) to load it.
"""
import sys

TARGET = sys.argv[1] if len(sys.argv) > 1 else '/app/apps/output/streaming_chunk_cache.py'
MARKER = 'touki-epg-hubfreeze'

IMPORTS_ANCHOR = (
    "import logging\n"
    "import time\n"
    "\n"
    "from django.http import StreamingHttpResponse\n"
    "\n"
    "logger = logging.getLogger(__name__)\n"
    "\n"
    "STATUS_BUILDING = \"building\"\n"
    "STATUS_READY = \"ready\"\n"
    "STATUS_ERROR = \"error\"\n"
    "\n"
    "DEFAULT_CACHE_TTL = 300\n"
)
IMPORTS_REPLACEMENT = (
    "import logging\n"
    "import os\n"
    "import time\n"
    "\n"
    "from django.http import StreamingHttpResponse\n"
    "\n"
    "logger = logging.getLogger(__name__)\n"
    "\n"
    "STATUS_BUILDING = \"building\"\n"
    "STATUS_READY = \"ready\"\n"
    "STATUS_ERROR = \"error\"\n"
    "\n"
    "# touki-epg-hubfreeze: EPG/M3U output cache lifetime. Raised from the upstream 300s\n"
    "# default: a 5-minute TTL forces a full >60s CPU-bound EPG rebuild ~12x/hour; EPG\n"
    "# data only changes when sources refresh (hours), so a longer cache is safe.\n"
    "DEFAULT_CACHE_TTL = int(os.environ.get(\"DISPATCHARR_OUTPUT_CACHE_TTL\", \"3600\"))\n"
)

with open(TARGET) as f:
    src = f.read()

if MARKER in src:
    print('already patched')
    sys.exit(0)

if IMPORTS_ANCHOR not in src:
    print(f"ERROR: anchor for 'imports/TTL' not found in {TARGET} - upstream changed")
    sys.exit(1)

src = src.replace(IMPORTS_ANCHOR, IMPORTS_REPLACEMENT, 1)

with open(TARGET, 'w') as f:
    f.write(src)
print('patched')
sys.exit(0)
