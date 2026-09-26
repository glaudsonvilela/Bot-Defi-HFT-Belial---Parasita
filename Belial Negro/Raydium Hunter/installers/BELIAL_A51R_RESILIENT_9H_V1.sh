#!/usr/bin/env bash
set -euo pipefail
umask 077

# BELIAL A5.1R — RESILIENT SEGMENTED 9H V1
#
# Canonical base:
#   4b67576f49f391b7847f18fb4b7c2ecd0f370ff8
#   feat(a51): clean runtime and add shadow satellites for 9h
#
# This installer does NOT rebuild A5.1 from A5. It starts from the already
# committed A5.1 package, preserving it, and creates an isolated resilient
# package/release.
#
# Recovery policy:
# - systemd user service + linger for reboot auto-start;
# - wait for /mnt/sherlock-ssd after boot before starting the manager;
# - PRIMARY-READY time is accumulated across fresh segments;
# - short primary-network gaps are recorded and do not count;
# - >=180 s primary gap triggers a fresh segment;
# - power/manager restart closes the old segment logically and starts a new one;
# - only complete PAPER journal records are replayed for audit;
# - PAPER positions are not carried across segment/power gaps;
# - missing market data is never fabricated;
# - Binance/Bybit/Kraken remain SHADOW only;
# - orders=false.

EXPECTED_HEAD="4b67576f49f391b7847f18fb4b7c2ecd0f370ff8"

BASE="${BELIAL_BASE:-/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter}"
REPO="${BELIAL_GIT_REPO:-$HOME/Área de trabalho/belial-github-publicar}"

BASE_REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a51-16q-9h-v1"
BASE_SNAPSHOT="$REPO/$BASE_REL"

NEW_REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a51r-16q-9h-v1"
NEW_SNAPSHOT="$REPO/$NEW_REL"

RELEASE_NAME="belial-a51r-16q-9h-v1"
DEST="$BASE/releases/$RELEASE_NAME"

INSTALLER_REL="Belial Negro/Raydium Hunter/installers/BELIAL_A51R_RESILIENT_9H_V1.sh"
INSTALLER_DEST="$REPO/$INSTALLER_REL"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE="$BASE/evidence/a51r-install-$TS"
TMP="$(mktemp -d -t belial-a51r-XXXXXX)"
EXPORT="$TMP/export"
CANON="$EXPORT/$BASE_REL"
WORK="$TMP/BELIAL_A51R_16Q_9H_V1"

SELF="$(readlink -f "$0")"

cleanup(){ rm -rf -- "$TMP"; }
trap cleanup EXIT

fail(){
  echo
  echo "BELIAL_A51R_FAILED=$1" >&2
  exit 3
}

[ "$(id -u)" -ne 0 ] || fail "DO_NOT_RUN_AS_ROOT"

for cmd in git python3 sha256sum find diff cp mkdir awk grep sed findmnt tee tail mv date readlink chmod tar systemctl loginctl journalctl pgrep; do
  command -v "$cmd" >/dev/null || fail "DEPENDENCY_MISSING:$cmd"
done

findmnt -rn --mountpoint /mnt/sherlock-ssd >/dev/null || fail "SSD_NOT_MOUNTED"
[ -d "$BASE" ] || fail "BELIAL_BASE_NOT_FOUND"
[ -d "$REPO/.git" ] || fail "GIT_REPO_NOT_FOUND"
[ -d "$BASE_SNAPSHOT" ] || fail "A51_BASE_SNAPSHOT_NOT_FOUND"

mkdir -p "$EVIDENCE" "$BASE/releases"

echo "============================================================"
echo " BELIAL A5.1R | AUTO-RESUME | 9 HORAS PRIMARY-READY"
echo "============================================================"

# ---------------------------------------------------------------------------
# 0. Current canonical Git baseline
# ---------------------------------------------------------------------------
echo "[0/9] Baseline Git A5.1 já commitado"

cd "$REPO"
HEAD_NOW="$(git rev-parse HEAD)"
echo "HEAD=$HEAD_NOW"
[ "$HEAD_NOW" = "$EXPECTED_HEAD" ] || fail "UNEXPECTED_HEAD:$HEAD_NOW"

if [ -n "$(git status --porcelain=v1)" ]; then
  git status --short
  fail "GIT_WORKTREE_NOT_CLEAN"
fi

if git fetch origin main >"$EVIDENCE/git-fetch.log" 2>&1; then
  REMOTE_NOW="$(git rev-parse origin/main)"
  echo "ORIGIN_MAIN=$REMOTE_NOW"
  [ "$REMOTE_NOW" = "$HEAD_NOW" ] || fail "LOCAL_REMOTE_DIVERGED"
else
  echo "GIT_FETCH_WARNING=true"
fi

# Never kill an already-running 9h experiment automatically.
if pgrep -af 'python(3)? .* -m a51\.supervisor' >"$EVIDENCE/existing-a51-supervisor.log" 2>&1; then
  cat "$EVIDENCE/existing-a51-supervisor.log"
  fail "EXISTING_A51_SUPERVISOR_PRESERVED"
fi

# ---------------------------------------------------------------------------
# 1. Export and verify canonical committed A5.1
# ---------------------------------------------------------------------------
echo "[1/9] Export canônico A5.1 @ $EXPECTED_HEAD"

mkdir -p "$EXPORT"
git archive --format=tar HEAD -- "$BASE_REL" | tar -xf - -C "$EXPORT"
[ -d "$CANON" ] || fail "CANONICAL_A51_EXPORT_MISSING"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export BELIAL_A1_NO_BROWSER=1

(
  cd "$CANON"
  PYTHONPATH="$CANON" python3 -B -m a51.verify --log-dir "$EVIDENCE/base-tests"
) | tee "$EVIDENCE/base-a51-verify.log"

grep -Fq "A51_PACKAGE_HASHES=PASS" "$EVIDENCE/base-a51-verify.log"   || fail "BASE_A51_HASH_GATE"
grep -Fq "A51_OFFLINE_VERIFIED=" "$EVIDENCE/base-a51-verify.log"   || fail "BASE_A51_TEST_GATE"

# ---------------------------------------------------------------------------
# 2. Create isolated resilient package
# ---------------------------------------------------------------------------
echo "[2/9] Derivando A5.1R sem alterar A5.1"

mkdir -p "$WORK"
cp -a "$CANON/." "$WORK/"
cp "$WORK/MANIFEST_SHA256.json" "$WORK/BASELINE_A51_MANIFEST_SHA256.json"

python3 - "$WORK" "$EXPECTED_HEAD" <<'PY'
from pathlib import Path
import hashlib,json,sys

root=Path(sys.argv[1]);base_commit=sys.argv[2]

run_manager='"""Resilient segmented runner for the Belial A5.1 9-hour PAPER/SHADOW experiment.\n\nSafety model:\n- nine hours means PRIMARY-READY observed time, not merely wall time;\n- short network gaps are recorded and do not count toward observed time;\n- a primary-path gap >= 180 s ends the current segment and starts a new one;\n- after power loss/reboot, the previous segment is preserved and audited;\n- only the durable complete prefix of the PAPER input journal is replayed;\n- PAPER positions are never carried across a segment/power gap;\n- no missing market interval is fabricated;\n- orders remain disabled.\n\nThe existing a51 supervisor/strategy code is not modified by this module.\n"""\nfrom __future__ import annotations\n\nimport argparse\nimport contextlib\nimport fcntl\nimport json\nimport os\nimport shutil\nimport signal\nimport subprocess\nimport sys\nimport time\n\nfrom pathlib import Path\n\nfrom a51.common import BASE, DURATION_MS, ROOT, atomic, clean_env, digest, now_ms, read_json, safe, terminate\nfrom a51.journal import read_verified\nfrom a51.replay import replay\n\nSCHEMA = "belial_a51_resilient_run_v1"\nTARGET_ACTIVE_MS = DURATION_MS\nPRIMARY_GAP_RESTART_MS = 180_000\nSTATE_SYNC_MS = 5_000\nPERF_SAMPLE_MS = 30_000\nPREPARATION_TIMEOUT_MS = 1_500_000\nMAX_WALL_MS = 86_400_000\nMAX_SEGMENTS = 48\n\n\ndef complete_prefix(data: bytes):\n    """Return newline-complete bytes and the count of discarded trailing bytes."""\n    if not data:\n        return b"", 0\n    if data.endswith(b"\\n"):\n        return data, 0\n    cut = data.rfind(b"\\n")\n    if cut < 0:\n        return b"", len(data)\n    return data[:cut + 1], len(data) - cut - 1\n\n\ndef primary_ready(platform):\n    if not isinstance(platform, dict):\n        return False\n    integrity = platform.get("integrity") or {}\n    coverage = platform.get("coverage") or {}\n    return bool(\n        integrity.get("ok") is True\n        and int(coverage.get("live_signal_sources") or 0) > 0\n        and int(coverage.get("usable_books") or 0) > 0\n        and int(coverage.get("paper_prerequisites_ready") or 0) > 0\n    )\n\n\ndef append_jsonl(path, row, durable=False):\n    path = safe(path)\n    path.parent.mkdir(parents=True, exist_ok=True)\n    raw = json.dumps(\n        row,\n        ensure_ascii=False,\n        sort_keys=True,\n        separators=(",", ":"),\n        allow_nan=False,\n    ) + "\\n"\n    with path.open("a", encoding="utf-8") as handle:\n        handle.write(raw)\n        handle.flush()\n        if durable:\n            os.fsync(handle.fileno())\n\n\ndef write_state(path, state):\n    state["updated_ms"] = now_ms()\n    atomic(path, state, durable=True)\n\n\ndef init_state(path, run_id):\n    path = Path(path)\n    if path.exists():\n        current = read_json(path, {}) or {}\n        if current.get("active"):\n            raise RuntimeError("ACTIVE_A51R_RUN_ALREADY_EXISTS")\n        raise RuntimeError("A51R_RUN_STATE_ALREADY_EXISTS")\n\n    created = now_ms()\n    run_root = BASE / "evidence" / ("a51r-resilient-" + run_id)\n    run_root.mkdir(parents=True, exist_ok=False)\n    state = {\n        "schema": SCHEMA,\n        "run_id": run_id,\n        "release": str(ROOT),\n        "run_root": str(run_root),\n        "target_active_ms": TARGET_ACTIVE_MS,\n        "accumulated_active_ms": 0,\n        "created_ms": created,\n        "updated_ms": created,\n        "last_manager_heartbeat_ms": created,\n        "active": True,\n        "completed": False,\n        "continuous_experiment": True,\n        "resume_mode": "SEGMENTED_SAFE_NO_GAP_FILL",\n        "paper_position_carry_across_segment_gap": False,\n        "transient_network_gap_may_preserve_open_paper_positions": True,\n        "orders": False,\n        "segments": [],\n        "gaps": [],\n        "current_segment": None,\n        "final_report": None,\n        "terminal_reason": None,\n    }\n    write_state(path, state)\n    return state\n\n\ndef salvage_segment(segment_path, run_root, segment_id):\n    """Audit a crashed segment without editing its original evidence."""\n    segment = Path(segment_path)\n    source = segment / "paper" / "inputs.jsonl"\n    config = segment / "paper" / "CONFIG_EFFECTIVE.json"\n    recovery = Path(run_root) / "recovery" / segment_id\n    recovery.mkdir(parents=True, exist_ok=True)\n\n    result = {\n        "available": False,\n        "original_segment_untouched": True,\n        "paper_position_carry_across_segment_gap": False,\n    }\n\n    try:\n        if not source.is_file() or not config.is_file():\n            result["reason"] = "PAPER_INPUT_OR_CONFIG_MISSING"\n            atomic(recovery / "RECOVERY.json", result, durable=True)\n            return result\n\n        raw = source.read_bytes()\n        prefix, dropped = complete_prefix(raw)\n        result["dropped_trailing_bytes"] = dropped\n        if not prefix:\n            result["reason"] = "NO_COMPLETE_INPUT_RECORD"\n            atomic(recovery / "RECOVERY.json", result, durable=True)\n            return result\n\n        repaired = recovery / "inputs.jsonl"\n        repaired.write_bytes(prefix)\n        shutil.copy2(config, recovery / "CONFIG_EFFECTIVE.json")\n\n        verified = sum(1 for _ in read_verified(repaired))\n        replay_dir = recovery / "replay"\n        replay_result = replay(repaired, replay_dir)\n        replay_state = read_json(replay_dir / "STATE.json", {}) or {}\n\n        open_positions = []\n        for cohort, group in (replay_state.get("groups") or {}).items():\n            for row in group.get("positions", []):\n                if row.get("closed_ms") is None:\n                    open_positions.append({\n                        "cohort": cohort,\n                        "id": row.get("id"),\n                        "symbol": row.get("symbol"),\n                        "venue": row.get("venue"),\n                        "policy": row.get("policy"),\n                        "scenario": row.get("scenario"),\n                        "opened_ms": row.get("opened_ms"),\n                        "censored_due_segment_gap": True,\n                        "carried_forward": False,\n                    })\n\n        result.update(\n            available=True,\n            verified_inputs=verified,\n            replay=replay_result,\n            source_input_sha256=digest(source),\n            repaired_input_sha256=digest(repaired),\n            open_positions_censored=len(open_positions),\n            positions=open_positions,\n        )\n    except Exception as exc:\n        result.update(\n            available=False,\n            reason="RECOVERY_REPLAY_FAILED",\n            error=str(exc)[:2000],\n        )\n\n    atomic(recovery / "RECOVERY.json", result, durable=True)\n    return result\n\n\ndef current_segment(state):\n    ident = state.get("current_segment")\n    if not ident:\n        return None\n    return next(\n        (row for row in state.get("segments", []) if row.get("id") == ident),\n        None,\n    )\n\n\ndef mark_orphan_after_restart(state_path, state):\n    segment = current_segment(state)\n    if not segment:\n        return\n    if segment.get("status") in (\n        "COMPLETE",\n        "FAILED",\n        "INTERRUPTED",\n        "CENSORED_GAP",\n    ):\n        state["current_segment"] = None\n        write_state(state_path, state)\n        return\n\n    end = now_ms()\n    start = int(\n        state.get("last_manager_heartbeat_ms")\n        or state.get("updated_ms")\n        or end\n    )\n    start = min(start, end)\n    gap = {\n        "kind": "POWER_OR_MANAGER_GAP",\n        "start_ms": start,\n        "end_ms": end,\n        "duration_ms": max(0, end - start),\n        "segment": segment["id"],\n        "missing_market_data": True,\n        "continuous_experiment": False,\n        "orders": False,\n    }\n    state["gaps"].append(gap)\n    state["continuous_experiment"] = False\n    segment["status"] = "CENSORED_GAP"\n    segment["ended_ms"] = end\n    segment["reason"] = "POWER_OR_MANAGER_RESTART"\n    segment["recovery"] = salvage_segment(\n        segment["session"],\n        state["run_root"],\n        segment["id"],\n    )\n    state["current_segment"] = None\n    append_jsonl(Path(state["run_root"]) / "events.jsonl", gap, durable=True)\n    write_state(state_path, state)\n\n\ndef start_segment(state_path, state):\n    if len(state["segments"]) >= MAX_SEGMENTS:\n        raise RuntimeError("A51R_MAX_SEGMENTS_REACHED")\n\n    number = len(state["segments"]) + 1\n    ident = f"seg-{number:02d}"\n    session = BASE / "evidence" / (\n        "a51r-9h-" + state["run_id"] + "-" + ident\n    )\n    if session.exists():\n        raise RuntimeError("A51R_SEGMENT_PATH_ALREADY_EXISTS:" + str(session))\n\n    log_dir = Path(state["run_root"]) / "segments"\n    log_dir.mkdir(parents=True, exist_ok=True)\n    log_path = log_dir / (ident + "-supervisor.log")\n    log_handle = log_path.open("a", buffering=1)\n\n    env = clean_env()\n    env.update(\n        PYTHONPATH=str(ROOT),\n        PYTHONDONTWRITEBYTECODE="1",\n        PYTHONUNBUFFERED="1",\n        BELIAL_A1_NO_BROWSER="1",\n    )\n    process = subprocess.Popen(\n        [\n            sys.executable,\n            "-B",\n            "-m",\n            "a51.supervisor",\n            "--session",\n            str(session),\n        ],\n        cwd=ROOT,\n        env=env,\n        stdout=log_handle,\n        stderr=subprocess.STDOUT,\n        start_new_session=True,\n    )\n\n    now = now_ms()\n    segment = {\n        "id": ident,\n        "session": str(session),\n        "supervisor_pid": process.pid,\n        "log": str(log_path),\n        "status": "PREPARING",\n        "started_manager_ms": now,\n        "capture_started_ms": None,\n        "ended_ms": None,\n        "active_ms": 0,\n        "primary_ready": False,\n        "primary_gap_started_ms": None,\n        "reason": None,\n        "returncode": None,\n        "recovery": None,\n    }\n    state["segments"].append(segment)\n    state["current_segment"] = ident\n    write_state(state_path, state)\n    append_jsonl(\n        Path(state["run_root"]) / "events.jsonl",\n        {\n            "kind": "SEGMENT_STARTED",\n            "at_ms": now,\n            "segment": ident,\n            "session": str(session),\n            "orders": False,\n        },\n        durable=True,\n    )\n    return process, log_handle, segment\n\n\ndef process_tree_metrics(root_pid):\n    rows = {}\n    for path in Path("/proc").glob("[0-9]*"):\n        try:\n            status = {}\n            for line in (path / "status").read_text(errors="replace").splitlines():\n                if ":" in line:\n                    key, value = line.split(":", 1)\n                    status[key] = value.strip()\n            pid = int(path.name)\n            ppid = int(status.get("PPid", "0"))\n            rss = int(status.get("VmRSS", "0 kB").split()[0])\n            raw = (path / "stat").read_text()\n            end = raw.rfind(")")\n            fields = raw[end + 2:].split()\n            hz = os.sysconf(os.sysconf_names["SC_CLK_TCK"])\n            cpu = (int(fields[11]) + int(fields[12])) / hz\n            rows[pid] = {\n                "pid": pid,\n                "ppid": ppid,\n                "rss_kb": rss,\n                "cpu_seconds": cpu,\n            }\n        except Exception:\n            pass\n\n    descendants = {root_pid}\n    changed = True\n    while changed:\n        changed = False\n        for pid, row in rows.items():\n            if pid not in descendants and row["ppid"] in descendants:\n                descendants.add(pid)\n                changed = True\n\n    processes = [rows[pid] for pid in descendants if pid in rows]\n    return {\n        "process_count": len(processes),\n        "rss_kb": sum(row["rss_kb"] for row in processes),\n        "cpu_seconds": sum(row["cpu_seconds"] for row in processes),\n    }\n\n\ndef sample_performance(state, process):\n    segment = current_segment(state)\n    if not segment:\n        return\n    session = Path(segment["session"])\n    books = read_json(session / "paper/books.json", {}) or {}\n    ui = read_json(session / "ui/status.json", {}) or {}\n    platform = read_json(session / "PLATFORM.json", {}) or {}\n    satellite = read_json(session / "satellites/state.json", {}) or {}\n\n    append_jsonl(\n        Path(state["run_root"]) / "performance.jsonl",\n        {\n            "at_ms": now_ms(),\n            "segment": segment["id"],\n            **process_tree_metrics(process.pid),\n            "messages_per_second": books.get("messages_per_second"),\n            "rust_to_paper_ms_p95": books.get("rust_to_paper_ms_p95"),\n            "ingest_queue": books.get("ingest_queue"),\n            "ui_max_payload_bytes": ui.get("maximum_payload_bytes"),\n            "ui_coalesced_payloads": ui.get("coalesced_payloads"),\n            "coverage": platform.get("coverage"),\n            "coverage_peak": platform.get("coverage_peak"),\n            "satellite_state": satellite.get("state"),\n            "satellite_venues": satellite.get("venues"),\n            "primary_ready": primary_ready(platform),\n            "orders": False,\n        },\n    )\n\n\ndef stop_child(process, timeout=190):\n    if process.poll() is None:\n        with contextlib.suppress(ProcessLookupError):\n            os.killpg(process.pid, signal.SIGINT)\n    try:\n        process.wait(timeout=timeout)\n    except subprocess.TimeoutExpired:\n        terminate(process, 15)\n\n\ndef finalize_report(state_path, state):\n    run_root = Path(state["run_root"])\n    perf = run_root / "performance.jsonl"\n    samples = []\n    if perf.exists():\n        for line in perf.read_text().splitlines():\n            if line.strip():\n                with contextlib.suppress(Exception):\n                    samples.append(json.loads(line))\n\n    def numeric(key):\n        return [\n            float(row[key])\n            for row in samples\n            if isinstance(row.get(key), (int, float))\n            and not isinstance(row.get(key), bool)\n        ]\n\n    def median(values):\n        if not values:\n            return None\n        values = sorted(values)\n        middle = len(values) // 2\n        if len(values) % 2:\n            return values[middle]\n        return (values[middle - 1] + values[middle]) / 2\n\n    # Sum CPU deltas within each segment instead of subtracting unrelated\n    # process trees across restarts.\n    cpu_by_segment = {}\n    for row in samples:\n        value = row.get("cpu_seconds")\n        ident = row.get("segment")\n        if ident and isinstance(value, (int, float)):\n            cpu_by_segment.setdefault(ident, []).append(float(value))\n    cpu_total = sum(\n        max(0.0, values[-1] - values[0])\n        for values in cpu_by_segment.values()\n        if len(values) >= 2\n    )\n\n    rss = numeric("rss_kb")\n    latency = numeric("rust_to_paper_ms_p95")\n    payload = numeric("ui_max_payload_bytes")\n    mps = numeric("messages_per_second")\n\n    report = {\n        "schema": "belial_a51r_resilient_9h_report_v1",\n        "run_id": state["run_id"],\n        "target_active_ms": state["target_active_ms"],\n        "accumulated_active_ms": state["accumulated_active_ms"],\n        "completed": state["completed"],\n        "continuous_experiment": state["continuous_experiment"],\n        "resume_mode": state["resume_mode"],\n        "paper_position_carry_across_segment_gap": False,\n        "transient_network_gap_may_preserve_open_paper_positions": True,\n        "gap_count": len(state["gaps"]),\n        "gaps": state["gaps"],\n        "segments": state["segments"],\n        "orders": False,\n        "paper": True,\n        "shadow": True,\n        "performance": {\n            "samples": len(samples),\n            "rss_peak_mb": max(rss) / 1024 if rss else None,\n            "rss_median_mb": median(rss) / 1024 if rss else None,\n            "cpu_seconds_sum_of_segment_deltas": cpu_total,\n            "messages_per_second_median": median(mps),\n            "rust_to_paper_ms_p95_median": median(latency),\n            "rust_to_paper_ms_p95_peak": max(latency) if latency else None,\n            "ui_max_payload_bytes_peak": max(payload) if payload else None,\n            "primary_ready_sample_ratio": (\n                sum(row.get("primary_ready") is True for row in samples)\n                / len(samples)\n                if samples else None\n            ),\n        },\n        "baseline_smoke25_reference": {\n            "rss_peak_mb": 871.7890625,\n            "rss_median_mb": 844.69140625,\n            "cpu_seconds_delta_over_1500s": 793.5400000000001,\n            "rust_to_paper_ms_p95_median": 8.972759,\n            "ui_http_ms_p95": 34.359661000053165,\n            "ui_max_payload_bytes_peak": 128977.0,\n        },\n    }\n    report_path = run_root / "A51R_RESILIENT_9H_REPORT.json"\n    atomic(report_path, report, durable=True)\n    state["final_report"] = str(report_path)\n    write_state(state_path, state)\n    return report_path\n\n\ndef run(state_path):\n    state_path = Path(state_path)\n    runtime = state_path.parent\n    runtime.mkdir(parents=True, exist_ok=True)\n    lock = (runtime / "run-manager.lock").open("a+")\n    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n\n    state = read_json(state_path)\n    if not isinstance(state, dict) or state.get("schema") != SCHEMA:\n        raise RuntimeError("A51R_RUN_STATE_INVALID")\n    if state.get("completed"):\n        return 0\n    if not state.get("active"):\n        raise RuntimeError("A51R_RUN_NOT_ACTIVE")\n\n    mark_orphan_after_restart(state_path, state)\n\n    stop_requested = False\n\n    def handle_stop(*_):\n        nonlocal stop_requested\n        stop_requested = True\n\n    signal.signal(signal.SIGTERM, handle_stop)\n    signal.signal(signal.SIGINT, handle_stop)\n\n    process = None\n    log_handle = None\n    last_clock = time.monotonic()\n    last_sync = 0\n    last_perf = 0\n    consecutive_failures = 0\n    wall_deadline = int(state["created_ms"]) + MAX_WALL_MS\n\n    try:\n        while state["accumulated_active_ms"] < state["target_active_ms"]:\n            now = now_ms()\n\n            if now >= wall_deadline:\n                if process:\n                    stop_child(process)\n                state["active"] = False\n                state["completed"] = False\n                state["terminal_reason"] = "MAX_24H_WALL_BUDGET_REACHED"\n                state["last_manager_heartbeat_ms"] = now_ms()\n                write_state(state_path, state)\n                report = finalize_report(state_path, state)\n                print("A51R_TERMINAL_INCOMPLETE=true", flush=True)\n                print("A51R_REPORT=" + str(report), flush=True)\n                return 4\n\n            if stop_requested:\n                if process:\n                    stop_child(process)\n                state["last_manager_heartbeat_ms"] = now_ms()\n                write_state(state_path, state)\n                return 0\n\n            if process is None:\n                process, log_handle, _ = start_segment(state_path, state)\n                last_clock = time.monotonic()\n                last_sync = now_ms()\n                last_perf = 0\n                continue\n\n            segment = current_segment(state)\n            session = Path(segment["session"])\n            platform = read_json(session / "PLATFORM.json", {}) or {}\n            session_state = read_json(session / "SESSION.json", {}) or {}\n            integrity = read_json(session / "INTEGRITY.json", {"ok": True}) or {"ok": True}\n\n            mono = time.monotonic()\n            dt_ms = int(max(0.0, min(5.0, mono - last_clock)) * 1000)\n            last_clock = mono\n            now = now_ms()\n            phase = session_state.get("phase")\n            ready = phase == "CAPTURING" and primary_ready(platform)\n\n            if phase == "CAPTURING" and segment["capture_started_ms"] is None:\n                segment["capture_started_ms"] = now\n                segment["status"] = "CAPTURING"\n\n            if ready:\n                state["accumulated_active_ms"] += dt_ms\n                segment["active_ms"] += dt_ms\n                if segment["primary_gap_started_ms"] is not None:\n                    gap_start = segment["primary_gap_started_ms"]\n                    gap = {\n                        "kind": "PRIMARY_NETWORK_GAP",\n                        "start_ms": gap_start,\n                        "end_ms": now,\n                        "duration_ms": now - gap_start,\n                        "segment": segment["id"],\n                        "missing_market_data": True,\n                        "segment_restarted": False,\n                        "continuous_experiment": False,\n                        "orders": False,\n                    }\n                    state["gaps"].append(gap)\n                    state["continuous_experiment"] = False\n                    append_jsonl(\n                        Path(state["run_root"]) / "events.jsonl",\n                        gap,\n                        durable=True,\n                    )\n                    segment["primary_gap_started_ms"] = None\n                segment["primary_ready"] = True\n                consecutive_failures = 0\n            elif phase == "CAPTURING":\n                segment["primary_ready"] = False\n                if segment["primary_gap_started_ms"] is None:\n                    segment["primary_gap_started_ms"] = now\n\n            restart_reason = None\n            if integrity.get("ok") is False:\n                restart_reason = "SEGMENT_INTEGRITY_FAILED"\n            elif (\n                segment["primary_gap_started_ms"] is not None\n                and now - segment["primary_gap_started_ms"]\n                >= PRIMARY_GAP_RESTART_MS\n            ):\n                restart_reason = "PRIMARY_PATH_GAP_OVER_180S"\n            elif (\n                phase != "CAPTURING"\n                and now - segment["started_manager_ms"]\n                >= PREPARATION_TIMEOUT_MS\n            ):\n                restart_reason = "SEGMENT_PREPARATION_TIMEOUT"\n\n            if state["accumulated_active_ms"] >= state["target_active_ms"]:\n                segment["reason"] = "TARGET_PRIMARY_READY_TIME_REACHED"\n                stop_child(process)\n            elif restart_reason:\n                # Long gap: do not carry PAPER positions through it. A graceful\n                # segment finalization happens first; next segment starts fresh.\n                segment["reason"] = restart_reason\n                if segment["primary_gap_started_ms"] is not None:\n                    gap = {\n                        "kind": "PRIMARY_NETWORK_GAP",\n                        "start_ms": segment["primary_gap_started_ms"],\n                        "end_ms": now,\n                        "duration_ms": now - segment["primary_gap_started_ms"],\n                        "segment": segment["id"],\n                        "missing_market_data": True,\n                        "segment_restarted": True,\n                        "continuous_experiment": False,\n                        "orders": False,\n                    }\n                    state["gaps"].append(gap)\n                    state["continuous_experiment"] = False\n                    append_jsonl(\n                        Path(state["run_root"]) / "events.jsonl",\n                        gap,\n                        durable=True,\n                    )\n                    segment["primary_gap_started_ms"] = None\n                stop_child(process)\n\n            if now - last_perf >= PERF_SAMPLE_MS and process.poll() is None:\n                sample_performance(state, process)\n                last_perf = now\n\n            if now - last_sync >= STATE_SYNC_MS:\n                state["last_manager_heartbeat_ms"] = now\n                write_state(state_path, state)\n                last_sync = now\n\n            if process.poll() is not None:\n                returncode = process.returncode\n                if log_handle and not log_handle.closed:\n                    log_handle.close()\n                log_handle = None\n\n                segment["returncode"] = returncode\n                segment["ended_ms"] = now_ms()\n\n                if state["accumulated_active_ms"] >= state["target_active_ms"]:\n                    segment["status"] = "COMPLETE"\n                    state["current_segment"] = None\n                    state["completed"] = True\n                    state["active"] = False\n                    state["last_manager_heartbeat_ms"] = now_ms()\n                    write_state(state_path, state)\n                    report = finalize_report(state_path, state)\n                    print("A51R_RESILIENT_9H_COMPLETE=true", flush=True)\n                    print("A51R_RESILIENT_REPORT=" + str(report), flush=True)\n                    return 0\n\n                summary = read_json(session / "SUMMARY.json", {}) or {}\n                if segment.get("reason"):\n                    segment["status"] = "INTERRUPTED"\n                elif summary.get("capture_started"):\n                    segment["status"] = "INTERRUPTED"\n                    segment["reason"] = "SUPERVISOR_ENDED_BEFORE_RUN_TARGET"\n                else:\n                    segment["status"] = "FAILED"\n                    segment["reason"] = "SUPERVISOR_ENDED_DURING_PREPARATION"\n\n                # If the supervisor could not produce its normal replay, audit\n                # the durable prefix here. Never edit original segment bytes.\n                if not (session / "REPLAY_CHECK.json").exists():\n                    segment["recovery"] = salvage_segment(\n                        session,\n                        state["run_root"],\n                        segment["id"],\n                    )\n\n                state["current_segment"] = None\n                state["last_manager_heartbeat_ms"] = now_ms()\n                write_state(state_path, state)\n                process = None\n                consecutive_failures += 1\n\n                backoff = min(\n                    300,\n                    15 * (2 ** min(consecutive_failures - 1, 4)),\n                )\n                append_jsonl(\n                    Path(state["run_root"]) / "events.jsonl",\n                    {\n                        "kind": "SEGMENT_RESTART_BACKOFF",\n                        "at_ms": now_ms(),\n                        "seconds": backoff,\n                        "segment": segment["id"],\n                        "reason": segment["reason"],\n                        "orders": False,\n                    },\n                    durable=True,\n                )\n                deadline = time.monotonic() + backoff\n                while time.monotonic() < deadline and not stop_requested:\n                    time.sleep(1)\n                continue\n\n            time.sleep(1)\n\n    finally:\n        if process and process.poll() is None:\n            stop_child(process)\n        if log_handle and not log_handle.closed:\n            log_handle.close()\n        lock.close()\n\n    return 0\n\n\ndef self_test():\n    prefix, dropped = complete_prefix(b\'{"a":1}\\n{"b":2}\')\n    assert prefix == b\'{"a":1}\\n\'\n    assert dropped == len(b\'{"b":2}\')\n    assert primary_ready({\n        "integrity": {"ok": True},\n        "coverage": {\n            "live_signal_sources": 1,\n            "usable_books": 1,\n            "paper_prerequisites_ready": 1,\n        },\n    })\n    assert not primary_ready({\n        "integrity": {"ok": True},\n        "coverage": {\n            "live_signal_sources": 0,\n            "usable_books": 1,\n            "paper_prerequisites_ready": 1,\n        },\n    })\n    return True\n\n\ndef main():\n    parser = argparse.ArgumentParser()\n    parser.add_argument("--state", type=Path)\n    parser.add_argument("--init", action="store_true")\n    parser.add_argument("--run-id")\n    parser.add_argument("--self-test", action="store_true")\n    args = parser.parse_args()\n\n    if args.self_test:\n        self_test()\n        print("A51R_RUN_MANAGER_SELF_TEST=PASS")\n        return 0\n    if args.state is None:\n        raise SystemExit("--state required")\n    if args.init:\n        if not args.run_id:\n            raise SystemExit("--run-id required with --init")\n        state = init_state(args.state, args.run_id)\n        print("A51R_RUN_INITIALIZED=true")\n        print("A51R_RUN_ROOT=" + state["run_root"])\n        return 0\n    return run(args.state)\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
recovery_test='import unittest\n\nfrom a51.run_manager import complete_prefix, primary_ready, self_test\n\n\nclass A51RRecoveryTests(unittest.TestCase):\n    def test_self_test(self):\n        self.assertTrue(self_test())\n\n    def test_complete_prefix_drops_only_partial_tail(self):\n        prefix, dropped = complete_prefix(b\'{"a":1}\\n{"b":2}\')\n        self.assertEqual(prefix, b\'{"a":1}\\n\')\n        self.assertEqual(dropped, len(b\'{"b":2}\'))\n\n    def test_complete_prefix_keeps_complete_file(self):\n        data = b\'{"a":1}\\n\'\n        prefix, dropped = complete_prefix(data)\n        self.assertEqual(prefix, data)\n        self.assertEqual(dropped, 0)\n\n    def test_primary_ready_requires_integrity(self):\n        platform = {\n            "integrity": {"ok": False},\n            "coverage": {\n                "live_signal_sources": 1,\n                "usable_books": 1,\n                "paper_prerequisites_ready": 1,\n            },\n        }\n        self.assertFalse(primary_ready(platform))\n\n    def test_primary_ready_requires_all_three_market_gates(self):\n        for missing in (\n            "live_signal_sources",\n            "usable_books",\n            "paper_prerequisites_ready",\n        ):\n            coverage = {\n                "live_signal_sources": 1,\n                "usable_books": 1,\n                "paper_prerequisites_ready": 1,\n            }\n            coverage[missing] = 0\n            self.assertFalse(primary_ready({\n                "integrity": {"ok": True},\n                "coverage": coverage,\n            }))\n\n\nif __name__ == "__main__":\n    unittest.main()\n'

(root/"a51/run_manager.py").write_text(run_manager,encoding="utf-8")
(root/"tests_a51/test_recovery.py").write_text(recovery_test,encoding="utf-8")

provenance={
    "schema":"belial_a51r_resilience_v1",
    "base_commit":base_commit,
    "base_snapshot":"belial-a51-16q-9h-v1",
    "base_manifest_sha256":hashlib.sha256(
        (root/"BASELINE_A51_MANIFEST_SHA256.json").read_bytes()
    ).hexdigest(),
    "strategy_changed":False,
    "thresholds_changed":False,
    "sizing_changed":False,
    "exit_policy_changed":False,
    "rust_changed":False,
    "satellites_changed":False,
    "orders":False,
    "recovery":"SEGMENTED_SAFE_NO_GAP_FILL",
    "primary_gap_restart_ms":180000,
    "power_reboot_auto_resume":True,
}
(root/"docs/RESILIENCE_A51R.json").write_text(
    json.dumps(provenance,indent=2,sort_keys=True)+"\n",
    encoding="utf-8",
)

# Prove every member covered by the base A5.1 manifest remains byte-identical.
baseline=json.loads((root/"BASELINE_A51_MANIFEST_SHA256.json").read_text())
for relative,expected in baseline["files"].items():
    path=root/relative
    if not path.is_file():
        raise SystemExit("A51R_BASE_MEMBER_MISSING:"+relative)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if digest!=expected:
        raise SystemExit("A51R_BASE_MEMBER_MUTATED:"+relative)

for path in list((root/"a51").rglob("*.py"))+list((root/"tests_a51").rglob("*.py")):
    compile(path.read_text(),str(path),"exec")

print("A51R_BASE_BYTE_IDENTICAL=true")
print("A51R_DERIVATION=PASS")
PY

# ---------------------------------------------------------------------------
# 3. Recovery contracts and offline tests
# ---------------------------------------------------------------------------
echo "[3/9] Testes de recuperação + regressão A5.1"

(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m a51.run_manager --self-test
) | tee "$EVIDENCE/run-manager-self-test.log"
grep -Fq "A51R_RUN_MANAGER_SELF_TEST=PASS" "$EVIDENCE/run-manager-self-test.log"   || fail "RUN_MANAGER_SELF_TEST_FAILED"

PYTHONPYCACHEPREFIX="$EVIDENCE/pycache"   python3 -B -m compileall -q -f "$WORK/a51" "$WORK/tests_a51"

(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m unittest discover -s tests_a51 -v
) >"$EVIDENCE/a51r-tests.log" 2>&1 || {
  tail -n 320 "$EVIDENCE/a51r-tests.log" >&2
  fail "A51R_TESTS_FAILED"
}

grep -Eq 'Ran [1-9][0-9]* tests?' "$EVIDENCE/a51r-tests.log"   || fail "A51R_TEST_COUNT_MISSING"
echo "A51R_TESTS=PASS"

# Semantic static safety contract.
python3 - "$WORK/a51/run_manager.py" <<'PY'
from pathlib import Path
import ast,sys
source=Path(sys.argv[1]).read_text()
ast.parse(source)
required=(
    "SEGMENTED_SAFE_NO_GAP_FILL",
    "POWER_OR_MANAGER_GAP",
    "PRIMARY_NETWORK_GAP",
    "PRIMARY_GAP_RESTART_MS = 180_000",
    "paper_position_carry_across_segment_gap",
    "salvage_segment",
    "complete_prefix",
)
missing=[x for x in required if x not in source]
if missing:raise SystemExit("A51R_RECOVERY_CONTRACT_MISSING:"+repr(missing))
if '"carried_forward": True' in source:raise SystemExit("A51R_GAP_POSITION_CARRY_FORBIDDEN")
if "--resume" in source:raise SystemExit("A51R_UNSAFE_INPLACE_RESUME")
print("A51R_RECOVERY_CONTRACT=PASS")
print("A51R_POSITION_CARRY_ACROSS_SEGMENT_GAP=false")
PY

# ---------------------------------------------------------------------------
# 4. Seal package and verify
# ---------------------------------------------------------------------------
echo "[4/9] Selando pacote resiliente"

python3 - "$WORK" <<'PY'
from pathlib import Path
import hashlib,json,sys
root=Path(sys.argv[1]);manifest=root/"MANIFEST_SHA256.json"
files={}
for path in sorted(root.rglob("*")):
    if not path.is_file():continue
    relative=str(path.relative_to(root))
    if relative=="MANIFEST_SHA256.json":continue
    h=hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda:handle.read(1<<20),b""):h.update(block)
    files[relative]=h.hexdigest()
doc={
    "schema":"belial_a51_16q_9h_package_v1",
    "variant":"resilient_segmented_v1",
    "base_commit":"4b67576f49f391b7847f18fb4b7c2ecd0f370ff8",
    "base_release":"belial-a51-16q-9h-v1",
    "duration_ms":32400000,
    "minutes":540,
    "requested_assets":16,
    "satellites":["binance","bybit","kraken"],
    "satellites_shadow_only":True,
    "resilient_segmented":True,
    "power_reboot_auto_resume":True,
    "primary_gap_restart_ms":180000,
    "paper_position_carry_across_segment_gap":False,
    "orders":False,
    "files":files,
}
manifest.write_text(json.dumps(doc,indent=2,sort_keys=True)+"\n")
print("A51R_MANIFEST_FILES="+str(len(files)))
PY

(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m a51.verify --log-dir "$EVIDENCE/sealed-tests"
) | tee "$EVIDENCE/a51r-sealed-verify.log"

grep -Fq "A51_PACKAGE_HASHES=PASS" "$EVIDENCE/a51r-sealed-verify.log"   || fail "A51R_SEALED_HASH_GATE"
grep -Fq "A51_OFFLINE_VERIFIED=" "$EVIDENCE/a51r-sealed-verify.log"   || fail "A51R_SEALED_TEST_GATE"

# Rust is intentionally unchanged; prove exact engine bytes against canonical A5.1.
diff -qr "$CANON/engine_a51" "$WORK/engine_a51"   >"$EVIDENCE/engine-diff.log" 2>&1 || {
    cat "$EVIDENCE/engine-diff.log" >&2
    fail "A51R_RUST_CHANGED_UNEXPECTEDLY"
  }
echo "A51R_RUST_REUSED_BYTE_IDENTICAL=true"

# ---------------------------------------------------------------------------
# 5. Install immutable resilient release
# ---------------------------------------------------------------------------
echo "[5/9] Instalando release imutável A5.1R"

if [ -e "$DEST" ]; then
  diff -qr "$WORK" "$DEST" >"$EVIDENCE/release-existing.diff" 2>&1 || {
    cat "$EVIDENCE/release-existing.diff" >&2
    fail "EXISTING_A51R_RELEASE_DIFFERS"
  }
  echo "A51R_RELEASE_REUSED_EXACT=true"
else
  STAGING="$BASE/releases/.a51r-staging-$TS"
  cp -a "$WORK" "$STAGING"
  (
    cd "$STAGING"
    PYTHONPATH="$STAGING" python3 -B -m a51.verify --hashes-only
  ) >"$EVIDENCE/release-hash-verify.log" 2>&1 || {
    cat "$EVIDENCE/release-hash-verify.log" >&2
    fail "A51R_STAGING_VERIFY_FAILED"
  }
  mv "$STAGING" "$DEST"
  echo "A51R_RELEASE_INSTALLED=$DEST"
fi

# ---------------------------------------------------------------------------
# 6. Git snapshot + staged-tree proof
# ---------------------------------------------------------------------------
echo "[6/9] Checkpoint Git resiliente"

if [ -e "$NEW_SNAPSHOT" ]; then
  diff -qr "$WORK" "$NEW_SNAPSHOT" >"$EVIDENCE/snapshot-existing.diff" 2>&1 || {
    cat "$EVIDENCE/snapshot-existing.diff" >&2
    fail "EXISTING_A51R_SNAPSHOT_DIFFERS"
  }
else
  mkdir -p "$(dirname "$NEW_SNAPSHOT")"
  cp -a "$WORK" "$NEW_SNAPSHOT"
fi

mkdir -p "$(dirname "$INSTALLER_DEST")" "$REPO/docs"
cp "$SELF" "$INSTALLER_DEST"
chmod 0755 "$INSTALLER_DEST"

cat > "$REPO/docs/HANDOFF_A51R_RESILIENT_9H.md" <<EOF
# Belial A5.1R — 9h resiliente

Base canônica: \`4b67576f49f391b7847f18fb4b7c2ecd0f370ff8\`.

- A5.1 Clean/Perf + Binance/Bybit/Kraken SHADOW preservado;
- Rust preservado byte-a-byte;
- estratégia/sizing/saídas inalterados;
- systemd user service + linger;
- retomada após reboot em novo segmento;
- gaps de rede/energia explícitos;
- tempo de gap não conta para a meta;
- >=180 s sem caminho primário reinicia o segmento;
- prefixo completo do journal PAPER é replayado para auditoria;
- posições PAPER não atravessam gap entre segmentos;
- nenhuma lacuna de mercado é inventada;
- orders=false.

A meta é 32.400.000 ms de tempo PRIMARY-READY observado.
EOF

cat > "$REPO/docs/CURRENT_STATE.md" <<EOF
# Bot Belial — Estado atual

## Linha

**BELIAL A5.1R 16Q / 9H RESILIENTE**

Base A5.1:
\`4b67576f49f391b7847f18fb4b7c2ecd0f370ff8 feat(a51): clean runtime and add shadow satellites for 9h\`

## Segurança

- PAPER / SHADOW;
- orders=false;
- satélites Binance/Bybit/Kraken somente contexto;
- estratégia/sizing/saídas inalterados;
- recuperação por segmentos, sem gap-fill;
- posições PAPER não atravessam gap de reboot/segmento.

## Execução

9 horas de tempo PRIMARY-READY observado.
Systemd user + linger retomam o run manager após reboot.
EOF

CHECKPOINT="$REPO/Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A51R_RESILIENT_9H.sha256"
(
  cd "$REPO"
  sha256sum     "$NEW_REL/MANIFEST_SHA256.json"     "$NEW_REL/BASELINE_A51_MANIFEST_SHA256.json"     "$NEW_REL/a51/run_manager.py"     "$NEW_REL/tests_a51/test_recovery.py"     "$INSTALLER_REL"     docs/HANDOFF_A51R_RESILIENT_9H.md     docs/CURRENT_STATE.md     > "$CHECKPOINT"
)

DEEP_LOG="$REPO/Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv"
if ! grep -Fq "A51R_RESILIENT_9H" "$DEEP_LOG"; then
  printf '%s\tA51R_RESILIENT_9H\tLOCAL_GATES_PASS\t%s\t%s\t%s\n'     "$TS" "$RELEASE_NAME"     "$(sha256sum "$DEST/MANIFEST_SHA256.json" | awk '{print $1}')"     "$(sha256sum "$INSTALLER_DEST" | awk '{print $1}')"     >> "$DEEP_LOG"
fi

git add -f -- "$NEW_REL"
git add --   "$INSTALLER_REL"   "Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A51R_RESILIENT_9H.sha256"   "Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv"   docs/HANDOFF_A51R_RESILIENT_9H.md   docs/CURRENT_STATE.md

# The copied historical tree contains exactly the same three inherited
# whitespace defects as its canonical base. Exclude only those three.
WS1=":(exclude)$NEW_REL/legacy/runner/tools/preflight_pools.py"
WS2=":(exclude)$NEW_REL/tests_a3/test_processes.py"
WS3=":(exclude)$NEW_REL/tests_a4/test_processes.py"
git diff --cached --check -- . "$WS1" "$WS2" "$WS3"   >"$EVIDENCE/git-diff-check.log" 2>&1 || {
    cat "$EVIDENCE/git-diff-check.log" >&2
    fail "NEW_WHITESPACE_ERROR"
  }

TREE_SHA="$(git write-tree)"
INDEX_EXPORT="$TMP/index"
mkdir -p "$INDEX_EXPORT"
git archive --format=tar "$TREE_SHA" -- "$NEW_REL" | tar -xf - -C "$INDEX_EXPORT"
INDEX_A51R="$INDEX_EXPORT/$NEW_REL"
(
  cd "$INDEX_A51R"
  PYTHONPATH="$INDEX_A51R" python3 -B -m a51.verify --hashes-only
) | tee "$EVIDENCE/staged-index.log"
grep -Fq "A51_PACKAGE_HASHES=PASS" "$EVIDENCE/staged-index.log"   || fail "STAGED_INDEX_HASH_GATE"
echo "A51R_STAGED_INDEX_CANONICAL=true"

git commit -m "feat(a51r): add resilient segmented 9h runner"
A51R_COMMIT="$(git rev-parse HEAD)"
git push origin main
git fetch origin main >/dev/null 2>&1 || true
REMOTE_AFTER="$(git rev-parse origin/main)"
[ "$REMOTE_AFTER" = "$A51R_COMMIT" ] || fail "REMOTE_HEAD_MISMATCH"
[ -z "$(git status --porcelain=v1)" ] || fail "WORKTREE_NOT_CLEAN_AFTER_COMMIT"

echo "A51R_GIT_CHECKPOINT=PASS"
echo "A51R_COMMIT_SHA=$A51R_COMMIT"

# ---------------------------------------------------------------------------
# 7. Install persistent systemd user service + linger
# ---------------------------------------------------------------------------
echo "[7/9] Instalando systemd + boot auto-resume"

# Re-check after lengthy validation; never collide with a foreground run.
if pgrep -af 'python(3)? .* -m a51\.supervisor' >"$EVIDENCE/pre-service-a51.log" 2>&1; then
  cat "$EVIDENCE/pre-service-a51.log"
  fail "A51_SUPERVISOR_APPEARED_DURING_INSTALL"
fi

RUNTIME="$BASE/runtime/belial-a51r"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_STATE="$RUNTIME/ACTIVE_RUN.json"
SERVICE_NAME="belial-a51r-9h.service"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/$SERVICE_NAME"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/belial-a51r-run-manager"
PYTHON_BIN="$(command -v python3)"

mkdir -p "$RUNTIME" "$UNIT_DIR" "$BIN_DIR"

if [ -e "$RUN_STATE" ]; then
  ACTIVE="$(python3 - "$RUN_STATE" <<'PY'
import json,sys
try:print("1" if json.load(open(sys.argv[1])).get("active") else "0")
except Exception:print("0")
PY
)"
  [ "$ACTIVE" = "0" ] || fail "A51R_ACTIVE_RUN_ALREADY_EXISTS"
  mv "$RUN_STATE" "$RUNTIME/ACTIVE_RUN.previous.$(date -u +%Y%m%dT%H%M%SZ).json"
fi

cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
set -euo pipefail

# User services may start before the external SSD is mounted after boot.
while ! findmnt -rn --mountpoint /mnt/sherlock-ssd >/dev/null 2>&1; do
  sleep 10
done

exec env   PYTHONPATH="$DEST"   PYTHONDONTWRITEBYTECODE=1   PYTHONUNBUFFERED=1   "$PYTHON_BIN" -B -m a51.run_manager --state "$RUN_STATE"
EOF
chmod 0755 "$LAUNCHER"

cat > "$UNIT" <<EOF
[Unit]
Description=Belial A5.1R resilient 9h PAPER/SHADOW
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
ExecStart=$LAUNCHER
Restart=on-failure
RestartSec=10
RestartPreventExitStatus=4
SuccessExitStatus=4
TimeoutStopSec=220
KillMode=control-group

[Install]
WantedBy=default.target
EOF

LINGER="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"
if [ "$LINGER" != "yes" ]; then
  echo "A51R_LINGER_ENABLING=true"
  if ! loginctl enable-linger "$USER" 2>"$EVIDENCE/linger.log"; then
    command -v sudo >/dev/null || fail "LINGER_REQUIRES_ADMIN"
    echo "Pode ser solicitada sua senha para habilitar auto-resume após reboot."
    sudo loginctl enable-linger "$USER"
  fi
fi

LINGER="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"
[ "$LINGER" = "yes" ] || fail "LINGER_NOT_ENABLED"
echo "A51R_SYSTEMD_LINGER=PASS"

(
  cd "$DEST"
  PYTHONPATH="$DEST" python3 -B -m a51.run_manager     --state "$RUN_STATE" --init --run-id "$RUN_ID"
) | tee "$EVIDENCE/run-init.log"
grep -Fq "A51R_RUN_INITIALIZED=true" "$EVIDENCE/run-init.log"   || fail "RUN_STATE_INIT_FAILED"

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME" >/dev/null
systemctl --user restart "$SERVICE_NAME"
sleep 2

systemctl --user is-active --quiet "$SERVICE_NAME" || {
  systemctl --user status "$SERVICE_NAME" --no-pager >&2 || true
  journalctl --user -u "$SERVICE_NAME" -n 120 --no-pager >&2 || true
  fail "A51R_SERVICE_NOT_ACTIVE"
}
echo "A51R_SYSTEMD_SERVICE=PASS"

# ---------------------------------------------------------------------------
# 8. First PRIMARY-READY segment
# ---------------------------------------------------------------------------
echo "[8/9] Aguardando primeiro segmento PRIMARY-READY"

WAIT_START="$(date +%s)"
ACTIVE_SESSION=""
URL=""
while :; do
  systemctl --user is-active --quiet "$SERVICE_NAME" || {
    journalctl --user -u "$SERVICE_NAME" -n 180 --no-pager >&2 || true
    fail "SERVICE_STOPPED_BEFORE_PRIMARY_READY"
  }

  RESULT="$(python3 - "$RUN_STATE" <<'PY'
from pathlib import Path
import json,sys
state=json.load(open(sys.argv[1]))
ident=state.get("current_segment")
segment=next((x for x in state.get("segments",[]) if x.get("id")==ident),{}) if ident else {}
session=Path(segment["session"]) if segment.get("session") else None
platform={};phase="";url=""
if session:
    try:platform=json.loads((session/"PLATFORM.json").read_text())
    except Exception:pass
    try:phase=json.loads((session/"SESSION.json").read_text()).get("phase","")
    except Exception:pass
    try:url=json.loads((session/"ui/URL.json").read_text()).get("url","")
    except Exception:pass
coverage=platform.get("coverage") or {}
ready=(
    phase=="CAPTURING"
    and (platform.get("integrity") or {}).get("ok") is True
    and int(coverage.get("live_signal_sources") or 0)>0
    and int(coverage.get("usable_books") or 0)>0
    and int(coverage.get("paper_prerequisites_ready") or 0)>0
)
print(("1" if ready else "0")+"|"+str(session or "")+"|"+url+"|"+phase+"|"+str(coverage.get("live_signal_sources") or 0)+"|"+str(coverage.get("usable_books") or 0)+"|"+str(coverage.get("paper_prerequisites_ready") or 0))
PY
)"
  IFS='|' read -r READY ACTIVE_SESSION URL PHASE LIVE BOOKS ELIGIBLE <<<"$RESULT"

  if [ "$READY" = "1" ]; then
    echo "A51R_PRIMARY_READY=PASS live=$LIVE books=$BOOKS ready=$ELIGIBLE"
    echo "A51R_ACTIVE_SESSION=$ACTIVE_SESSION"
    [ -n "$URL" ] && echo "DASHBOARD_URL=$URL"
    if [ -n "$URL" ] && command -v xdg-open >/dev/null 2>&1 && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
      (xdg-open "$URL" >/dev/null 2>&1 || true) &
    fi
    break
  fi

  NOW="$(date +%s)"
  if [ $((NOW-WAIT_START)) -ge 1500 ]; then
    journalctl --user -u "$SERVICE_NAME" -n 260 --no-pager >&2 || true
    fail "FIRST_SEGMENT_NOT_PRIMARY_READY_WITHIN_25_MIN"
  fi
  sleep 2
done

# ---------------------------------------------------------------------------
# 9. Handoff
# ---------------------------------------------------------------------------
echo "[9/9] Execução entregue ao systemd"

HANDOFF="$EVIDENCE/RESILIENT_HANDOFF.txt"
cat > "$HANDOFF" <<EOF
A51R_COMMIT_SHA=$A51R_COMMIT
SERVICE_NAME=$SERVICE_NAME
RUN_STATE=$RUN_STATE
ACTIVE_SESSION=$ACTIVE_SESSION
DASHBOARD_URL=${URL:-}
TARGET_PRIMARY_READY_MS=32400000
RESUME_MODE=SEGMENTED_SAFE_NO_GAP_FILL
POWER_REBOOT_AUTO_RESUME=true
PRIMARY_GAP_RESTART_SECONDS=180
PAPER_POSITION_CARRY_ACROSS_SEGMENT_GAP=false
TRANSIENT_NETWORK_GAP_MAY_PRESERVE_OPEN_PAPER_POSITIONS=true
ORDERS=false
EOF

echo
echo "============================================================"
echo " BELIAL A5.1R | 9H RESILIENTE EM EXECUÇÃO"
echo "============================================================"
cat "$HANDOFF"
echo
echo "O terminal pode ser fechado."
echo "Após reboot, o serviço aguarda o SSD montar e retoma o run manager."
echo
echo "Status:"
echo "  systemctl --user status $SERVICE_NAME"
echo "Logs:"
echo "  journalctl --user -u $SERVICE_NAME -f"
echo "Estado:"
echo "  $RUN_STATE"
