#!/usr/bin/env python3
"""
Idempotent patches for Dispatcharr /app/apps/channels/tasks.py.

Applied on every saltbox_mod run so they survive container recreates
(dev image pulls). Each patch checks for a marker before applying.

PATCH 15 — DVB teletext captions branched to a sidecar (2026-08-31)
PATCH 16 — HLS playlist wall-clock anchors + restart discontinuity marks (2026-09-03)
PATCH 17 — caption-state producer: <final>.captions.json per attempt, from
the capture's own stream table (2026-09-13, docs/CAPTION-STATE.md in the
breakarr repo — reviewed and applied). A missing PATCH 15 sidecar today means,
indistinguishably, no subtitle PID / capture died early / ffmpeg wrote zero
bytes; this types the difference so breakarr's catalog can tell them apart.

PATCH 18 (a-d) — recording_end moved past the remux + final metadata save,
enriched with outcome/has_file/failure_reason/file_*/bytes_written/
start_time/end_time (2026-09-15). Mirrors upstream PR #1679 (v8eta/Dispatcharr
-> Dispatcharr/Dispatcharr dev, https://github.com/Dispatcharr/Dispatcharr/pull/1679,
review-requested reshape: enrich the existing event and re-time it, rather
than add recording_finalized/recording_failed as new terminal events),
applied here locally ahead of merge. Each sub-patch's marker is a fragment of
#1679's own reviewed diff, so once the dev image ships the merge, all four
recognise their effect already exists and no-op cleanly -- delete this PATCH
18(a-d) block then, it will already be redundant. 18a adds the payload
builder; 18b removes the early (pre-remux) emit; 18c is the enriched emit
after the metadata save; 18d adds the same emit to the crash-recovery path in
recover_recordings_on_startup, previously silent on a remux done at startup.

PATCH 18 (e-f) — playlist snapshot relocation (2026-09-15). NOT part of
#1679 and does NOT retire on that merge: purely local, permanent. PATCH 16's
wall-clock-anchored playlist used to be snapshotted by the breakarr_bridge
plugin's on_recording_end handler, at the (old, early) recording_end -- the
one moment the HLS scratch dir still existed. PATCH 18b/c moves recording_end
past the point where a successful remux sweeps that dir, breaking the
snapshot for every successful recording. 18e captures it here instead, right
before the sweep, into custom_properties; 18f threads it into the same
recording_end payload 18c builds, re-anchored on 18c's own text so it keeps
applying even after #1679 merges (18c retires, 18f does not).

RETIRED 2026-09-11 — PATCHES 1, 2 and 5 (filename placeholders, HDHR output
profile on the DVR capture, {episode_title}). All three merged upstream on
2026-09-07 (PRs 1615 and 1619): the dev image now carries {sub_title},
{start_date}/{start_year}/{start_month}/{start_day} and
CoreSettings.get_dvr_output_profile_id(), read from
dvr_settings.output_profile_id. The settings, not a patch, now select the
profile and the placeholders. The 64c61f3 rewrite of run_recording moved every
anchor the three relied on, so from 2026-09-09 to 2026-09-11 the driver exited
1 on 'dvr_output_profile' and the whole play failed with NOTHING applied
(nightly runs 03:03, 03:08, 03:14 on 2026-09-11). The same rewrite added the
explicit "-map 0:v?"/"-map 0:a?" that PATCH 15's second entry used to insert,
so that entry is gone too; the remaining three PATCH 15 entries were
re-anchored to the rewritten function and call site.

Both surviving patches carry real "# PATCH <n>" comments so `markers()` in
dispatcharr-update.sh sees them.

RETIRED 2026-08-28 — PATCHES 3, 4, 6, 7 and 8 (Plex scan after comskip; Plex
scan before comskip; skip comskip on ABC; AU FTA news mark-mode + news-specific
ini; skip comskip on a partial recording).

Every one of them hangs off the comskip path, and comskip is OFF: measured on
this instance, CoreSettings.get_dvr_comskip_enabled() is False, so
comskip_process_recording is never dispatched and not one of the five could
run. Ad detection moved to breakarr, which does its own per-second detection
and produces the cuts, so PATCH 4's ABC skip and PATCH 7's news tuning have no
job left. PATCH 7 was unreachable twice over: /data/comskip-news.ini and
/data/comskip-10news.ini no longer exist in the container.

⚠️ A dead patch is NOT harmless, which is why these were deleted rather than
left in place. Each entry is an exact-string anchor into upstream's tasks.py,
and the driver below exits 1 the moment an anchor stops matching — which fails
the ENTIRE play, not just that patch. That is precisely how PATCH 14
(2026-08-27) and PATCHES 12/13 (2026-08-28) killed three consecutive nightly
runs, each leaving every later task unapplied.

Verified before removal by simulating this driver against the PRISTINE dev
image (docker run --entrypoint cat ... /app/apps/channels/tasks.py), not
against the running container — the running container has every marker already
applied, so it reports "no-op" for all ten and proves nothing. Against the
pristine image all ten still applied cleanly, so these five were LATENT
tripwires rather than active ones, and the remaining three apply cleanly on
their own. Re-run that simulation before adding or removing any patch here.

Run: docker exec dispatcharr python3 /tmp/patch-tasks.py
Exit: 0 = ok (applied and/or already present), 1 = an anchor was not found
"""
import sys

TARGET = '/app/apps/channels/tasks.py'

PATCHES = [
    # --- PATCH 15: DVB teletext captions to a sidecar (2026-08-31) -----------
    # AU FTA carries dvb_teletext, and captions separate programme from
    # advertising DIRECTLY rather than by proxy. They never reach a recording:
    # output profile 6 maps only 0:v and 0:a:0, so the subtitle dies at the
    # proxy. Mapping it through does NOT degrade a recording, it DESTROYS one --
    # both muxers in the DVR path refuse the codec, measured on this host:
    #   HLS       [webvtt] Exactly one WebVTT stream is needed -> no output file
    #   matroska  Tag [6][0][0][0] incompatible with codec id  -> 0-byte .mkv
    # and the capture is "-c copy" with no -map, so default stream selection
    # would grab the subtitle and fail at the FIRST step. Same for the
    # Passthrough profile, which uses "-map 0".
    #
    # So: one input, two outputs. The HLS output names its streams explicitly
    # (strictly safer than today, which relies on default selection), and
    # teletext branches to its own mpegts sidecar as a pure copy -- this
    # container's ffmpeg has NO teletext decoder; the host's is built
    # --enable-libzvbi and decodes afterwards via breakarr-captions.
    #
    # Split into three entries with distinct markers so each applies and
    # no-ops independently. A single large anchor over the whole function
    # would be far more brittle, and a stale anchor fails the ENTIRE play.
    # (Re-anchored 2026-09-11 to the 64c61f3 rewrite: the signature is now
    # multi-line with a user_agent kwarg, and upstream itself maps
    # "0:v?"/"0:a?" so the explicit-maps entry was dropped.)
    {
        'name': 'teletext_sidecar_signature',
        'marker': 'subs_path=None',
        'anchor': (
            '    hls_start_number,\n'
            '    user_agent=None,\n'
            '):\n'
            '    """Build the FFmpeg command for DVR HLS segment recording."""\n'
            '    from core.utils import dispatcharr_dvr_user_agent\n'
            '\n'
            '    if not user_agent:\n'
            '        user_agent = dispatcharr_dvr_user_agent(recording_id)\n'
            '    return ['
        ),
        'replacement': (
            '    hls_start_number,\n'
            '    user_agent=None,\n'
            '    subs_path=None,\n'
            '):\n'
            '    """Build the FFmpeg command for DVR HLS segment recording."""\n'
            '    # PATCH 15: teletext sidecar. Builds a list now instead of\n'
            '    # returning one, so a second output can be appended below.\n'
            '    from core.utils import dispatcharr_dvr_user_agent\n'
            '\n'
            '    if not user_agent:\n'
            '        user_agent = dispatcharr_dvr_user_agent(recording_id)\n'
            '    cmd = ['
        ),
    },
    {
        'name': 'teletext_sidecar_output',
        'marker': '# PATCH 15: second output',
        'anchor': (
            '        "-hls_segment_filename", hls_seg_pattern,\n'
            '        hls_m3u8,\n'
            '    ]'
        ),
        'replacement': (
            '        "-hls_segment_filename", hls_seg_pattern,\n'
            '        hls_m3u8,\n'
            '    ]\n'
            '    if subs_path:\n'
            '        # PATCH 15: second output on the SAME input -- no extra\n'
            '        # connection to the proxy and no extra tuner. Pure copy,\n'
            '        # because this container cannot decode teletext. "0:s?" is\n'
            '        # optional, so a source without teletext produces an empty\n'
            '        # sidecar rather than failing the recording.\n'
            '        cmd += ["-map", "0:s?", "-c:s", "copy", "-f", "mpegts", subs_path]\n'
            '    return cmd'
        ),
    },
    {
        'name': 'teletext_sidecar_callsite',
        'marker': '_subs_path',
        'anchor': (
            '            ffmpeg_cmd = _dvr_build_ffmpeg_cmd(\n'
            '                stream_url,\n'
            '                recording_id,\n'
            '                hls_m3u8,\n'
            '                hls_seg_pattern,\n'
            '                hls_start_number,\n'
            '                user_agent=ffmpeg_user_agent,\n'
            '            )'
        ),
        'replacement': (
            '            # PATCH 15: hls_dir sits beside final_path, so the sidecar\n'
            '            # lands next to the finished recording -- where\n'
            '            # breakarr-captions looks for it -- and survives the rmtree\n'
            '            # that removes hls_dir. Named per ATTEMPT: the DVR restarts\n'
            '            # ffmpeg on an outage, and a single path would be truncated,\n'
            '            # silently discarding every caption before the outage.\n'
            '            _subs_path = None\n'
            '            if final_path:\n'
            '                _subs_path = "%s.subs.%05d.ts" % (final_path, hls_start_number)\n'
            '            ffmpeg_cmd = _dvr_build_ffmpeg_cmd(\n'
            '                stream_url,\n'
            '                recording_id,\n'
            '                hls_m3u8,\n'
            '                hls_seg_pattern,\n'
            '                hls_start_number,\n'
            '                user_agent=ffmpeg_user_agent,\n'
            '                subs_path=_subs_path,\n'
            '            )'
        ),
    },
    {
        # PATCH 16 -- wall-clock anchors and restart marks in the DVR's HLS
        # playlist (2026-09-03). The chain re-timestamps the stream three times
        # (profile-6 transcode, this capture with make_zero, the concat with
        # make_zero again), so a finished .mkv carries no broadcast timing and a
        # restart is invisible in it. `program_date_time` stamps the first
        # segment of every ATTEMPT with the wall clock at muxer start (chrony on
        # the host makes that comparable with the EPG); `discont_start` marks a
        # retry (start_number > 0) so the playlist records the splice the
        # concat will hide. Every segment already carries #EXTINF, so the media
        # time of any restart and the wall-clock instant of media time 0 follow
        # by addition. The .mkv is untouched. The playlist snapshot itself
        # moved to PATCH 18e (2026-09-15) -- recording_end no longer fires
        # before the scratch dir is swept, so it is captured just before the
        # sweep instead, inside run_recording, not at the event.
        # ffmpeg 8.1.2 in the container lists both flags (-h muxer=hls).
        'name': 'hls_timing_anchors',
        'marker': '# PATCH 16',
        'anchor': (
            '        "-hls_flags", "append_list+omit_endlist+independent_segments",\n'
        ),
        'replacement': (
            '        # PATCH 16: wall-clock anchor per attempt + discontinuity mark on\n'
            '        # retries, so the playlist records what the concat erases.\n'
            '        "-hls_flags", ("append_list+omit_endlist+independent_segments"\n'
            '                       "+program_date_time"\n'
            '                       + ("+discont_start" if hls_start_number else "")),\n'
        ),
    },
    # --- PATCH 17: caption-state producer (2026-09-13) -----------------------
    # Producer side of the caption-state contract designed in
    # docs/CAPTION-STATE.md (breakarr repo), reviewed and applied now. A
    # missing PATCH 15 sidecar means, indistinguishably: no subtitle PID (a
    # real measurement), the capture died early, or ffmpeg wrote zero bytes.
    # This parses the capture's OWN stderr stream table -- never a second
    # connection to the proxy -- into a typed <final>.captions.json per
    # attempt, so breakarr's catalog can tell those apart instead of reading
    # every one of them as "this channel carries no captions".
    {
        'name': 'caption_state_helpers',
        'marker': 'header=None',
        'anchor': (
            'def _dvr_drain_ffmpeg_stderr(proc, rec_id, tail):\n'
            '    """Drain FFmpeg stderr in a background thread (see run_recording for rationale)."""\n'
            '    try:\n'
            '        buf = bytearray()\n'
            '        stream = proc.stderr\n'
            '        while True:\n'
            '            byte = stream.read(1)\n'
            '            if not byte:\n'
            '                break\n'
            '            if byte in (b\'\\r\', b\'\\n\'):\n'
            '                if buf:\n'
            '                    line = buf.decode(\'utf-8\', errors=\'replace\').strip()\n'
            '                    buf.clear()\n'
            '                    if line:\n'
            '                        tail.append(line)\n'
            '                        low = line.lower()\n'
            '                        if \'error\' in low or \'failed\' in low or \'invalid\' in low:\n'
            '                            logger.warning(f"DVR recording {rec_id} ffmpeg: {line}")\n'
            '                        else:\n'
            '                            logger.debug(f"DVR recording {rec_id} ffmpeg: {line}")\n'
            '                continue\n'
            '            buf.append(byte[0])\n'
            '            if len(buf) > 4096:\n'
            '                line = buf.decode(\'utf-8\', errors=\'replace\').strip()\n'
            '                buf.clear()\n'
            '                if line:\n'
            '                    tail.append(line)\n'
            '                    logger.debug(f"DVR recording {rec_id} ffmpeg: {line}")\n'
            '        if buf:\n'
            '            line = buf.decode(\'utf-8\', errors=\'replace\').strip()\n'
            '            if line:\n'
            '                tail.append(line)\n'
            '                logger.debug(f"DVR recording {rec_id} ffmpeg: {line}")\n'
            '    except Exception as _de:\n'
            '        logger.debug(f"DVR recording {rec_id}: stderr drain ended: {_de}")'
        ),
        'replacement': (
            '_DVR_STREAM_TABLE_RE = re.compile(\n'
            '    r"^\\s*Stream #0:(\\d+)\\S*: (Video|Audio|Subtitle): (\\w+)")\n'
            '\n'
            '\n'
            'def _dvr_caption_state(stderr_text, subs_path, attempt):\n'
            '    """What the capture\'s OWN stream table said about subtitles.\n'
            '\n'
            '    The table is bounded: it starts at "Input #0," and ends at the first\n'
            '    line that is not a Stream/Metadata/Duration continuation ("Output #0,"\n'
            '    in practice). If that terminator never arrives the process died mid-table\n'
            '    and we know NOTHING -- which is stream_table_truncated, not "no subtitles".\n'
            '    """\n'
            '    now = datetime.now().astimezone().isoformat(timespec="seconds")\n'
            '    base = {"schema": 1, "attempt": attempt, "observed_at": now}\n'
            '    if "Input #0," not in stderr_text:\n'
            '        return dict(base, state="absent", reason="capture_incomplete")\n'
            '    body = stderr_text.split("Input #0,", 1)[1]\n'
            '    if "Output #0," not in body:\n'
            '        return dict(base, state="absent", reason="stream_table_truncated")\n'
            '    codec = None\n'
            '    for line in body.split("Output #0,", 1)[0].splitlines():\n'
            '        m = _DVR_STREAM_TABLE_RE.match(line)\n'
            '        if m and m.group(2) == "Subtitle":\n'
            '            codec = m.group(3)\n'
            '            break\n'
            '    if codec is None:\n'
            '        return dict(base, state="absent", reason="no_subtitle_stream")\n'
            '    size = os.path.getsize(subs_path) if subs_path and os.path.exists(subs_path) else 0\n'
            '    if not size:\n'
            '        return dict(base, state="absent", reason="sidecar_empty")\n'
            '    return dict(base, state="present", codec=codec,\n'
            '                sidecar=os.path.basename(subs_path), bytes=size)\n'
            '\n'
            '\n'
            'def _dvr_write_caption_state(final_path, doc):\n'
            '    """One file per recording, rewritten per attempt. Written atomically: a\n'
            '    half-written document must be a hard refusal downstream, not a silent\n'
            '    absent, and a rename cannot be observed half-done."""\n'
            '    if not final_path:\n'
            '        return\n'
            '    tmp = "%s.captions.json.tmp" % final_path\n'
            '    with open(tmp, "w") as fh:\n'
            '        json.dump(doc, fh, sort_keys=True)\n'
            '    os.replace(tmp, "%s.captions.json" % final_path)\n'
            '\n'
            '\n'
            'def _dvr_drain_ffmpeg_stderr(proc, rec_id, tail, header=None):\n'
            '    """Drain FFmpeg stderr in a background thread (see run_recording for rationale).\n'
            '\n'
            '    PATCH 17: when `header` is given, every line is also appended there until\n'
            '    the capture\'s own stream table (Input #0, ... Output #0,) completes -- a\n'
            '    separate, unbounded-until-then buffer, so the header survives independent\n'
            '    of `tail`\'s 200-line rolling eviction on a recording of any length.\n'
            '    """\n'
            '    _header_done = header is None\n'
            '    try:\n'
            '        buf = bytearray()\n'
            '        stream = proc.stderr\n'
            '        while True:\n'
            '            byte = stream.read(1)\n'
            '            if not byte:\n'
            '                break\n'
            '            if byte in (b\'\\r\', b\'\\n\'):\n'
            '                if buf:\n'
            '                    line = buf.decode(\'utf-8\', errors=\'replace\').strip()\n'
            '                    buf.clear()\n'
            '                    if line:\n'
            '                        tail.append(line)\n'
            '                        if not _header_done:\n'
            '                            header.append(line)\n'
            '                            if line.startswith("Output #0,"):\n'
            '                                _header_done = True\n'
            '                        low = line.lower()\n'
            '                        if \'error\' in low or \'failed\' in low or \'invalid\' in low:\n'
            '                            logger.warning(f"DVR recording {rec_id} ffmpeg: {line}")\n'
            '                        else:\n'
            '                            logger.debug(f"DVR recording {rec_id} ffmpeg: {line}")\n'
            '                continue\n'
            '            buf.append(byte[0])\n'
            '            if len(buf) > 4096:\n'
            '                line = buf.decode(\'utf-8\', errors=\'replace\').strip()\n'
            '                buf.clear()\n'
            '                if line:\n'
            '                    tail.append(line)\n'
            '                    if not _header_done:\n'
            '                        header.append(line)\n'
            '                        if line.startswith("Output #0,"):\n'
            '                            _header_done = True\n'
            '                    logger.debug(f"DVR recording {rec_id} ffmpeg: {line}")\n'
            '        if buf:\n'
            '            line = buf.decode(\'utf-8\', errors=\'replace\').strip()\n'
            '            if line:\n'
            '                tail.append(line)\n'
            '                if not _header_done:\n'
            '                    header.append(line)\n'
            '                logger.debug(f"DVR recording {rec_id} ffmpeg: {line}")\n'
            '    except Exception as _de:\n'
            '        logger.debug(f"DVR recording {rec_id}: stderr drain ended: {_de}")'
        ),
    },
    {
        'name': 'caption_state_thread_header',
        'marker': '_stream_table_header',
        'anchor': (
            '            if ffmpeg_proc is not None and ffmpeg_proc.stderr is not None:\n'
            '                _stderr_thread = threading.Thread(\n'
            '                    target=_dvr_drain_ffmpeg_stderr,\n'
            '                    args=(ffmpeg_proc, recording_id, _ffmpeg_stderr_tail),\n'
            '                    daemon=True,\n'
            '                    name=f"dvr-ffmpeg-stderr-{recording_id}-{_ffmpeg_retry_count}",\n'
            '                )\n'
            '                _stderr_thread.start()'
        ),
        'replacement': (
            '            # PATCH 17: fresh per-attempt buffers for the caption-state header\n'
            '            # capture and its thread handle (must exist even if launch failed).\n'
            '            _stream_table_header = []\n'
            '            _stderr_thread = None\n'
            '            if ffmpeg_proc is not None and ffmpeg_proc.stderr is not None:\n'
            '                _stderr_thread = threading.Thread(\n'
            '                    target=_dvr_drain_ffmpeg_stderr,\n'
            '                    args=(ffmpeg_proc, recording_id, _ffmpeg_stderr_tail, _stream_table_header),\n'
            '                    daemon=True,\n'
            '                    name=f"dvr-ffmpeg-stderr-{recording_id}-{_ffmpeg_retry_count}",\n'
            '                )\n'
            '                _stderr_thread.start()'
        ),
    },
    {
        'name': 'caption_state_write',
        'marker': 'caption state not written',
        'anchor': (
            '            _dvr_ensure_ffmpeg_exited(ffmpeg_proc)\n'
            '\n'
            '            if _break_reason in ("end_time", "stopped", "deleted"):\n'
            '                break'
        ),
        'replacement': (
            '            _dvr_ensure_ffmpeg_exited(ffmpeg_proc)\n'
            '            # PATCH 17: producer side of the caption-state contract\n'
            '            # (docs/CAPTION-STATE.md in the breakarr repo) -- one typed\n'
            '            # <final>.captions.json per attempt, from this capture\'s own\n'
            '            # stream table. Never allowed to affect the recording itself.\n'
            '            if _stderr_thread is not None:\n'
            '                _stderr_thread.join(timeout=2)\n'
            '            try:\n'
            '                _last_caption_state = _dvr_caption_state(\n'
            '                    "\\n".join(_stream_table_header), _subs_path, hls_start_number,\n'
            '                )\n'
            '                _dvr_write_caption_state(final_path, _last_caption_state)\n'
            '            except Exception as _cs_e:\n'
            '                logger.warning(\n'
            '                    f"DVR recording {recording_id}: caption state not written: {_cs_e}"\n'
            '                )\n'
            '\n'
            '            if _break_reason in ("end_time", "stopped", "deleted"):\n'
            '                break'
        ),
    },
    {
        # PATCH 17 (cont.): _last_caption_state must exist before the retry
        # loop, so a recording that never enters it (or whose last attempt's
        # write failed) still has a defined value to persist below -- None
        # reads as UNKNOWN to breakarr, exactly like a missing .captions.json.
        'name': 'caption_state_var_init',
        'marker': '_last_caption_state = None',
        'anchor': (
            '    _ffmpeg_stderr_tail = deque(maxlen=200)\n'
        ),
        'replacement': (
            '    _ffmpeg_stderr_tail = deque(maxlen=200)\n'
            '    _last_caption_state = None\n'
        ),
    },
    {
        # PATCH 17 (cont.): the breakarr_bridge plugin payload (_captions(cp))
        # reads cp["captions"]/cp["captions_codec"] the same way _health/_stream
        # read their own summary fields off the recording row -- so a consumer
        # of the event never has to go to the filesystem for what the LAST
        # attempt's stream table said.
        'name': 'caption_state_summary',
        'marker': 'cp["captions_codec"]',
        'anchor': (
            '        cp["bytes_written"] = bytes_written\n'
            '        cp["remux_success"] = remux_success'
        ),
        'replacement': (
            '        cp["bytes_written"] = bytes_written\n'
            '        cp["remux_success"] = remux_success\n'
            '        if _last_caption_state is not None:\n'
            '            cp["captions"] = {\n'
            '                "state": _last_caption_state.get("state"),\n'
            '                "reason": _last_caption_state.get("reason"),\n'
            '            }\n'
            '            cp["captions_codec"] = _last_caption_state.get("codec")'
        ),
    },
    # --- PATCH 18a: recording_end payload builder (2026-09-15) --------------
    {
        'name': 'recording_end_payload_builder',
        'marker': 'def _dvr_recording_end_payload',
        'anchor': (
            '_DVR_HLS_REMUX_TIMEOUT_SECONDS = 30 * 60\n'
            '\n'
            '\n'
            'def _dvr_build_hls_playlist_remux_cmd(m3u8_path, output_path, extra_args=None):'
        ),
        'replacement': (
            '_DVR_HLS_REMUX_TIMEOUT_SECONDS = 30 * 60\n'
            '\n'
            '\n'
            'def _dvr_recording_end_payload(cp, final_path, remux_success, start_time=None, end_time=None):\n'
            '    """Enriched recording_end payload (PATCH 18a, mirrors upstream PR #1679).\n'
            '    Never raises: a malformed cp/path degrades to "unknown"/None fields.\n'
            '    """\n'
            '    props = cp if isinstance(cp, dict) else {}\n'
            '\n'
            '    status = props.get("status")\n'
            '    if not isinstance(status, str) or not status:\n'
            '        status = "unknown"\n'
            '\n'
            '    bytes_written = props.get("bytes_written")\n'
            '    if isinstance(bytes_written, bool) or not isinstance(bytes_written, int):\n'
            '        bytes_written = None\n'
            '\n'
            '    reason = props.get("interrupted_reason")\n'
            '    if not isinstance(reason, str) or not reason:\n'
            '        reason = None\n'
            '\n'
            '    remux_success = bool(remux_success)\n'
            '    path = final_path if isinstance(final_path, str) and final_path else None\n'
            '    file_size = None\n'
            '    if path:\n'
            '        try:\n'
            '            file_size = os.path.getsize(path)\n'
            '        except OSError:\n'
            '            file_size = None\n'
            '\n'
            '    file_name = props.get("file_name")\n'
            '    if not isinstance(file_name, str) or not file_name:\n'
            '        file_name = os.path.basename(path) if path else None\n'
            '\n'
            '    if not remux_success:\n'
            '        failure_reason = "remux_failed"\n'
            '    elif not path or file_size is None:\n'
            '        failure_reason = "missing_file"\n'
            '    elif file_size == 0:\n'
            '        failure_reason = "empty_file"\n'
            '    else:\n'
            '        failure_reason = None\n'
            '\n'
            '    has_file = failure_reason is None\n'
            '\n'
            '    return {\n'
            '        "outcome": "success" if has_file else "failed",\n'
            '        "has_file": has_file,\n'
            '        "failure_reason": failure_reason,\n'
            '        "status": status,\n'
            '        "interrupted_reason": reason,\n'
            '        "file_path": path,\n'
            '        "file_name": file_name,\n'
            '        "file_url": props.get("file_url") if isinstance(props.get("file_url"), str) else None,\n'
            '        "file_size": file_size,\n'
            '        "remux_success": remux_success,\n'
            '        "bytes_written": bytes_written,\n'
            '        "start_time": start_time if isinstance(start_time, str) else None,\n'
            '        "end_time": end_time if isinstance(end_time, str) else None,\n'
            '    }\n'
            '\n'
            '\n'
            'def _dvr_build_hls_playlist_remux_cmd(m3u8_path, output_path, extra_args=None):'
        ),
    },
    # --- PATCH 18b: remove the early (pre-remux) recording_end emit ---------
    {
        'name': 'recording_end_remove_early_emit',
        'marker': 'PATCH 18b: recording_end moved past the remux',
        'anchor': (
            '    # Log system event for recording end\n'
            '    try:\n'
            '        from core.utils import log_system_event\n'
            '        log_system_event(\n'
            "            'recording_end',\n"
            '            channel_id=channel.uuid,\n'
            '            channel_name=channel.name,\n'
            '            recording_id=recording_id,\n'
            '            interrupted=interrupted,\n'
            '            bytes_written=bytes_written\n'
            '        )\n'
            '    except Exception as e:\n'
            '        logger.error(f"Could not log recording end event: {e}")'
        ),
        'replacement': (
            '    # PATCH 18b: recording_end moved past the remux + final metadata save\n'
            '    # (PATCH 18c below) -- firing it here announced a file that did not yet\n'
            '    # exist in final form. Mirrors upstream PR #1679.'
        ),
    },
    # --- PATCH 18c: enriched recording_end, fired after the metadata save ---
    {
        'name': 'recording_end_enriched_emit',
        'marker': '**_dvr_recording_end_payload(',
        'anchor': (
            '        _db_retry(\n'
            '            _save_final_metadata,\n'
            '            max_retries=_dvr_db_max_retries,\n'
            '            base_interval=_dvr_db_retry_interval,\n'
            '            label=f"DVR recording {recording_id}: metadata save",\n'
            '        )\n'
            '\n'
            '        # Notify frontends so the UI refreshes immediately (e.g. "Stopped" \u2192 "Completed")'
        ),
        'replacement': (
            '        _db_retry(\n'
            '            _save_final_metadata,\n'
            '            max_retries=_dvr_db_max_retries,\n'
            '            base_interval=_dvr_db_retry_interval,\n'
            '            label=f"DVR recording {recording_id}: metadata save",\n'
            '        )\n'
            '\n'
            '        # PATCH 18c: recording_end, enriched, fired here instead of at\n'
            '        # ffmpeg stop -- see PATCH 18b. Mirrors upstream PR #1679.\n'
            '        try:\n'
            '            from core.utils import log_system_event\n'
            '            log_system_event(\n'
            "                'recording_end',\n"
            '                channel_id=channel.uuid,\n'
            '                channel_name=channel.name,\n'
            '                recording_id=recording_id,\n'
            '                **_dvr_recording_end_payload(\n'
            '                    cp, final_path, remux_success,\n'
            '                    start_time=start_time.isoformat(), end_time=end_time.isoformat(),\n'
            '                ),\n'
            '                # PATCH 18f: local-only, NOT part of #1679 -- threads PATCH\n'
            '                # 18e\'s playlist snapshot into the same event. Re-anchored on\n'
            '                # this line, so it keeps applying after #1679 merges even\n'
            '                # though 18c itself retires.\n'
            '                hls_playlist_snapshot=cp.get("_hls_playlist_snapshot"),\n'
            '            )\n'
            '        except Exception as e:\n'
            '            logger.error(f"Could not log recording end event: {e}")\n'
            '\n'
            '        # Notify frontends so the UI refreshes immediately (e.g. "Stopped" \u2192 "Completed")'
        ),
    },
    # --- PATCH 18d: same emit from the crash-recovery finalize path ---------
    {
        'name': 'recording_end_recovery_emit',
        'marker': 'recording end event for recovered recording',
        'anchor': (
            '                rec.custom_properties = cp\n'
            '                _db_retry(\n'
            '                    lambda r=rec: r.save(update_fields=["custom_properties"]),\n'
            '                    label=f"DVR recovery: recording {rec.id} expired status update",\n'
            '                )\n'
            '            except Exception as e:\n'
            '                logger.warning(f"Failed to finalize expired recording {rec.id}: {e}")'
        ),
        'replacement': (
            '                rec.custom_properties = cp\n'
            '                _db_retry(\n'
            '                    lambda r=rec: r.save(update_fields=["custom_properties"]),\n'
            '                    label=f"DVR recovery: recording {rec.id} expired status update",\n'
            '                )\n'
            '\n'
            '                # PATCH 18d: recording_end must also fire from crash recovery\n'
            '                # (mirrors PR #1679) -- a remux completed here was previously\n'
            '                # silent. No playlist snapshot on this path (PATCH 18e only\n'
            '                # runs in run_recording itself) -- crash recovery is rare\n'
            '                # enough that this is not worth the extra scope.\n'
            '                try:\n'
            '                    from core.utils import log_system_event\n'
            '                    log_system_event(\n'
            "                        'recording_end',\n"
            '                        channel_id=rec.channel.uuid,\n'
            '                        channel_name=rec.channel.name,\n'
            '                        recording_id=rec.id,\n'
            '                        **_dvr_recording_end_payload(\n'
            '                            cp, mkv_path, cp.get("remux_success"),\n'
            '                            start_time=rec.start_time.isoformat() if rec.start_time else None,\n'
            '                            end_time=rec.end_time.isoformat() if rec.end_time else None,\n'
            '                        ),\n'
            '                    )\n'
            '                except Exception as _ev_e:\n'
            '                    logger.error(\n'
            '                        f"Could not log recording end event for recovered recording {rec.id}: {_ev_e}"\n'
            '                    )\n'
            '            except Exception as e:\n'
            '                logger.warning(f"Failed to finalize expired recording {rec.id}: {e}")'
        ),
    },
    # --- PATCH 18e: capture the playlist snapshot before the sweep ----------
    {
        'name': 'playlist_snapshot_var_init',
        'marker': '_hls_playlist_snapshot = None',
        'anchor': (
            '    # --- Post-processing: remux HLS playlist \u2192 final MKV ---\n'
            '    remux_success = False\n'
        ),
        'replacement': (
            '    # --- Post-processing: remux HLS playlist \u2192 final MKV ---\n'
            '    remux_success = False\n'
            '    # PATCH 18e: playlist snapshot, captured just before the scratch dir\n'
            '    # is swept below -- PATCH 18b/18c moved recording_end past that\n'
            '    # sweep, so this is the only remaining moment it exists to read.\n'
            '    # Local only; not part of upstream PR #1679.\n'
            '    _hls_playlist_snapshot = None\n'
        ),
    },
    {
        'name': 'playlist_snapshot_capture',
        'marker': 'PATCH 18e: snapshot the playlist',
        'anchor': (
            '                try:\n'
            '                    shutil.rmtree(hls_dir)\n'
            '                    logger.debug(f"Cleaned up HLS directory: {hls_dir}")\n'
        ),
        'replacement': (
            '                try:\n'
            '                    # PATCH 18e: snapshot the playlist before it is swept.\n'
            '                    try:\n'
            '                        with open(hls_m3u8, "rb") as _pf:\n'
            '                            _hls_playlist_snapshot = _pf.read(1_000_000).decode(\n'
            '                                "utf-8", errors="replace")\n'
            '                    except OSError:\n'
            '                        pass\n'
            '                    shutil.rmtree(hls_dir)\n'
            '                    logger.debug(f"Cleaned up HLS directory: {hls_dir}")\n'
        ),
    },
    {
        'name': 'playlist_snapshot_persist',
        'marker': 'cp["_hls_playlist_snapshot"]',
        'anchor': (
            '        cp["bytes_written"] = bytes_written\n'
            '        cp["remux_success"] = remux_success\n'
        ),
        'replacement': (
            '        cp["bytes_written"] = bytes_written\n'
            '        cp["remux_success"] = remux_success\n'
            '        if _hls_playlist_snapshot:\n'
            '            cp["_hls_playlist_snapshot"] = _hls_playlist_snapshot\n'
        ),
    },
]

with open(TARGET) as f:
    src = f.read()

# Remove the old PATCH 4b Seven-News *skip* block if present. Vestigial since
# PATCH 7 was retired (2026-08-28) and unreachable on a container recreated from
# the image, which never carries the block; kept only as a cheap safety net for a
# hand-patched container. Delete once no such container can exist.
OLD_SKIP = (
    "        _prog = cp.get('program') if isinstance(cp, dict) else None\n"
    "        _ptitle = (_prog.get('title') if isinstance(_prog, dict) else '') or ''\n"
    "        if 'seven news' in _ptitle.lower():\n"
    "            cp['comskip'] = {'status': 'skipped', 'reason': 'comskip_unreliable_au_news'}\n"
    "            Recording.objects.filter(pk=recording_id).update(custom_properties=cp)\n"
    "            _ws('skipped', {'reason': 'comskip_unreliable_au_news'})\n"
    "            return 'skipped_comskip_unreliable'\n"
)
removed_old = False
if OLD_SKIP in src:
    src = src.replace(OLD_SKIP, "")
    removed_old = True

changed = removed_old
for p in PATCHES:
    if p['marker'] in src:
        continue
    if p['anchor'] not in src:
        print(f"ERROR: anchor for patch '{p['name']}' not found - upstream changed")
        sys.exit(1)
    src = src.replace(p['anchor'], p['replacement'])
    changed = True

if changed:
    with open(TARGET, 'w') as f:
        f.write(src)
    print('patched')
else:
    print('already patched')
