"""Build-time gate, not a patch: the patched image's migration graph must load with one leaf
per app, or the build fails and nothing is pinned or installed.

Added 2026-09-26 after 809fe55 crash-looped on filemonster: upstream took our subtitle-copy fix
as core/0029_update_default_stream_and_output_profiles while this series still carried
0029_merge_20260914_1210, and Django refused to start ("Conflicting migrations detected").
Every other check passed; only starting the image showed it. This runs the same graph check
Django runs at startup, inside the build, with no database: MigrationLoader(None) reads the
migration files only.

Prints `applied` last when the graph is clean (the report word the forge verify accepts);
exits 1 with the conflicting migrations named when it is not.
"""
import os
import sys

sys.path.insert(0, "/app")
os.chdir("/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dispatcharr.settings")
os.environ.setdefault("DJANGO_SECRET_KEY", "build-time-migration-check")

import django  # noqa: E402

django.setup()

from django.db.migrations.loader import MigrationLoader  # noqa: E402

loader = MigrationLoader(None, ignore_no_migrations=True)
conflicts = loader.detect_conflicts()
if conflicts:
    for app, names in sorted(conflicts.items()):
        print(f"CONFLICT {app}: {', '.join(sorted(names))}")
    print("ANCHOR-MISSING: the migration graph has more than one leaf; the image would not start")
    sys.exit(1)
print(f"{len(loader.disk_migrations)} migrations, one leaf per app")
print("applied")
