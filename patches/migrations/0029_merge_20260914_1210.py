# Merge migration resolving two 0028 leaf nodes in `core`:
#   - 0028_add_subtitle_copy_to_default_profiles  (local saltbox_mod patch,
#     see projects/dispatcharr-dvb-subtitles.md, pending upstream merge)
#   - 0028_alter_systemevent_event_type           (upstream, arrived via the
#     rolling ghcr.io/dispatcharr/dispatcharr:dev tag)
#
# No operations: this only linearises the graph so `manage.py migrate` has a
# single leaf again. Delete this file (and the local 0028 patch) once the
# subtitle fix merges upstream and the fork patch is retired.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_add_subtitle_copy_to_default_profiles"),
        ("core", "0028_alter_systemevent_event_type"),
    ]

    operations = []
