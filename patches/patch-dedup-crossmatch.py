#!/usr/bin/env python3
"""
Idempotent patch for Dispatcharr /app/apps/channels/tasks.py.

PATCH 11 - dedup an identifiable airing against IDENTITY-LESS bookings
           (2026-08-06; RE-ROLLED 2026-08-18 onto the approved upstream shape)

  The upstream fix (PR #1506, approved by SergeantPanda as commit c8805df4) wires
  episode identity and the start-time window as an if/else:

      if prog_ident:
          if (tvg_id, title, prog_ident) in existing_episode_keys: continue
      else:
          ... start-time window ...

  so an airing that HAS an identity never consults the window. Its comment argues
  that is safe because "an identifiable episode that is not already scheduled is a
  genuinely different airing". That holds only if every already-scheduled recording
  can be identified. It cannot: any recording whose snapshot predates the identity
  field has no season/episode in custom_properties["program"], so _identity_of()
  returns None for it, it contributes NO entry to existing_episode_keys, and it
  lands in existing_program_index instead - which the identity branch never reads.

  Observed 2026-08-06: the EPG reissued its programme IDs and nudged starts ~30s.
  Seven News came back as a new programme with identity s26e218, found no
  episode-key match (the existing booking 32581 had program.season=None), skipped
  the window by design, and was scheduled a SECOND time 30s away. Both bookings
  resolved to the same output path, so at recording-end two concurrent
  `ffmpeg -y -f concat` remuxes wrote one file and destroyed it - a 3900s
  recording became 1125s of garbage. Highway Patrol Special s2e2 hit the identical
  gap the same night.

  Fix: run the window after an episode-key MISS regardless of whether this airing
  is identifiable. existing_program_index is still populated only from
  identity-less recordings, so the original safety property is preserved exactly:
  an identifiable episode can never be suppressed by another identifiable one. It
  can now be suppressed by an identity-less booking of the same channel+title
  inside the window - precisely the ambiguous case, and the one that corrupts
  recordings.

  Reported upstream on PR #1506; drop this patch if it is fixed there.

Run:  docker exec dispatcharr python3 /tmp/patch-dedup-crossmatch.py
Test: python3 patch-dedup-crossmatch.py <copy-of-tasks.py>
Exit: 0 = ok (applied and/or already present), 1 = anchor not found
"""
import sys

TARGET = sys.argv[1] if len(sys.argv) > 1 else '/app/apps/channels/tasks.py'
MARKER = 'PATCH 11: window runs after an episode-key miss'

with open(TARGET) as f:
    src = f.read()

if MARKER in src:
    print('already patched')
    sys.exit(0)

# SUPERSEDED UPSTREAM (detected 2026-08-23, Dispatcharr 0.29.0)
#   Upstream adopted this fix. tasks.py now reads exactly what this patch used to
#   produce -- the episode-key test is a combined `if prog_ident and (...)`, so a
#   MISS falls through to the start-time window whether or not the airing is
#   identifiable -- and the upstream comment now states the same reasoning this
#   patch argued for on PR #1506. Upstream went further, adding
#   claimed_program_index_entries so each orphan claims at most one candidate.
#
#   This is why the 2026-08-23 run failed with ANCHOR NOT FOUND: the old if/else
#   the patch rewrites no longer exists. That was the patch working correctly --
#   refusing to touch code it no longer understands rather than mispatching it.
#
#   Kept as a no-op rather than deleted so that a future upstream regression is
#   still caught: if neither the upstream shape nor the old anchor is present,
#   this exits 1 and says so.
UPSTREAM_FIXED = (
    '                if prog_ident and (\n'
    '                    (str(prog.tvg_id), prog_title_l, prog_ident) in existing_episode_keys\n'
    '                ):\n'
    '                    continue\n'
)
if UPSTREAM_FIXED in src:
    print('superseded upstream (0.29.0+): episode-key miss already falls through '
          'to the start-time window; nothing to patch')
    sys.exit(0)

START = (
    '                if prog_ident:\n'
    '                    if (str(prog.tvg_id), prog_title_l, prog_ident) in existing_episode_keys:\n'
    '                        continue\n'
    '                else:\n'
)
END = '                        pass  # naive/aware mismatch in stored data: fall through\n'

i = src.find(START)
if i < 0:
    print('ANCHOR NOT FOUND - identity/window if-else block missing or changed; re-roll this patch',
          file=sys.stderr)
    sys.exit(1)
j = src.find(END, i)
if j < 0:
    print('ANCHOR NOT FOUND - window block tail missing; re-roll this patch', file=sys.stderr)
    sys.exit(1)

body = src[i + len(START): j + len(END)]
# the else-body sits one level deeper; lift it out of the removed else
lifted = ''.join(
    (ln[4:] if ln.startswith('                    ') else ln)
    for ln in body.splitlines(keepends=True)
)

# The upstream comment on the window opens "No identity at all:" and claims it
# "can never suppress an identifiable episode". Both are false once the window
# runs unconditionally, and a comment that contradicts its code is how the
# original hole survived review. Restate what is actually still guaranteed.
STALE = (
    '                # No identity at all: a narrow start-time window is the only\n'
    '                # signal left. Compared solely against other identity-less\n'
    '                # airings, so this can never suppress an identifiable episode.\n'
)
FRESH = (
    '                # existing_program_index is populated ONLY from identity-less\n'
    '                # recordings, so an identifiable episode still cannot be\n'
    '                # suppressed by another identifiable one -- only by an\n'
    '                # identity-less booking of the same channel+title inside the\n'
    '                # window, which is the ambiguous case that corrupts files.\n'
)
if STALE in lifted:
    lifted = lifted.replace(STALE, FRESH, 1)
else:
    print('WARNING: window comment not in the expected form, left as-is', file=sys.stderr)

head = (
    '                # PATCH 11: window runs after an episode-key miss REGARDLESS of\n'
    '                # identity. A booking made before the snapshot carried season/episode\n'
    '                # is identity-LESS, contributes nothing to existing_episode_keys, and\n'
    '                # would otherwise be invisible to an identifiable candidate - which on\n'
    '                # 2026-08-06 scheduled Seven News twice 30s apart and destroyed the\n'
    '                # recording when both remuxes wrote the same output path.\n'
    '                if prog_ident and (\n'
    '                    (str(prog.tvg_id), prog_title_l, prog_ident) in existing_episode_keys\n'
    '                ):\n'
    '                    continue\n'
)

src = src[:i] + head + lifted + src[j + len(END):]
with open(TARGET, 'w') as f:
    f.write(src)
print('patched')
