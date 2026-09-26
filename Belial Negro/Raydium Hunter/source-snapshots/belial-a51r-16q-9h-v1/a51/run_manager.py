"""Resilient segmented runner for the Belial A5.1 9-hour PAPER/SHADOW experiment.

Safety model:
- nine hours means PRIMARY-READY observed time, not merely wall time;
- short network gaps are recorded and do not count toward observed time;
- a primary-path gap >= 180 s ends the current segment and starts a new one;
- after power loss/reboot, the previous segment is preserved and audited;
- only the durable complete prefix of the PAPER input journal is replayed;
- PAPER positions are never carried across a segment/power gap;
- no missing market interval is fabricated;
- orders remain disabled.

The existing a51 supervisor/strategy code is not modified by this module.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time

from pathlib import Path

from a51.common import BASE, DURATION_MS, ROOT, atomic, clean_env, digest, now_ms, read_json, safe, terminate
from a51.journal import read_verified
from a51.replay import replay

SCHEMA = "belial_a51_resilient_run_v1"
TARGET_ACTIVE_MS = DURATION_MS
PRIMARY_GAP_RESTART_MS = 180_000
STATE_SYNC_MS = 5_000
PERF_SAMPLE_MS = 30_000
PREPARATION_TIMEOUT_MS = 1_500_000
MAX_WALL_MS = 86_400_000
MAX_SEGMENTS = 48


def complete_prefix(data: bytes):
    """Return newline-complete bytes and the count of discarded trailing bytes."""
    if not data:
        return b"", 0
    if data.endswith(b"\n"):
        return data, 0
    cut = data.rfind(b"\n")
    if cut < 0:
        return b"", len(data)
    return data[:cut + 1], len(data) - cut - 1


def primary_ready(platform):
    if not isinstance(platform, dict):
        return False
    integrity = platform.get("integrity") or {}
    coverage = platform.get("coverage") or {}
    return bool(
        integrity.get("ok") is True
        and int(coverage.get("live_signal_sources") or 0) > 0
        and int(coverage.get("usable_books") or 0) > 0
        and int(coverage.get("paper_prerequisites_ready") or 0) > 0
    )


def append_jsonl(path, row, durable=False):
    path = safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(
        row,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(raw)
        handle.flush()
        if durable:
            os.fsync(handle.fileno())


def write_state(path, state):
    state["updated_ms"] = now_ms()
    atomic(path, state, durable=True)


def init_state(path, run_id):
    path = Path(path)
    if path.exists():
        current = read_json(path, {}) or {}
        if current.get("active"):
            raise RuntimeError("ACTIVE_A51R_RUN_ALREADY_EXISTS")
        raise RuntimeError("A51R_RUN_STATE_ALREADY_EXISTS")

    created = now_ms()
    run_root = BASE / "evidence" / ("a51r-resilient-" + run_id)
    run_root.mkdir(parents=True, exist_ok=False)
    state = {
        "schema": SCHEMA,
        "run_id": run_id,
        "release": str(ROOT),
        "run_root": str(run_root),
        "target_active_ms": TARGET_ACTIVE_MS,
        "accumulated_active_ms": 0,
        "created_ms": created,
        "updated_ms": created,
        "last_manager_heartbeat_ms": created,
        "active": True,
        "completed": False,
        "continuous_experiment": True,
        "resume_mode": "SEGMENTED_SAFE_NO_GAP_FILL",
        "paper_position_carry_across_segment_gap": False,
        "transient_network_gap_may_preserve_open_paper_positions": True,
        "orders": False,
        "segments": [],
        "gaps": [],
        "current_segment": None,
        "final_report": None,
        "terminal_reason": None,
    }
    write_state(path, state)
    return state


def salvage_segment(segment_path, run_root, segment_id):
    """Audit a crashed segment without editing its original evidence."""
    segment = Path(segment_path)
    source = segment / "paper" / "inputs.jsonl"
    config = segment / "paper" / "CONFIG_EFFECTIVE.json"
    recovery = Path(run_root) / "recovery" / segment_id
    recovery.mkdir(parents=True, exist_ok=True)

    result = {
        "available": False,
        "original_segment_untouched": True,
        "paper_position_carry_across_segment_gap": False,
    }

    try:
        if not source.is_file() or not config.is_file():
            result["reason"] = "PAPER_INPUT_OR_CONFIG_MISSING"
            atomic(recovery / "RECOVERY.json", result, durable=True)
            return result

        raw = source.read_bytes()
        prefix, dropped = complete_prefix(raw)
        result["dropped_trailing_bytes"] = dropped
        if not prefix:
            result["reason"] = "NO_COMPLETE_INPUT_RECORD"
            atomic(recovery / "RECOVERY.json", result, durable=True)
            return result

        repaired = recovery / "inputs.jsonl"
        repaired.write_bytes(prefix)
        shutil.copy2(config, recovery / "CONFIG_EFFECTIVE.json")

        verified = sum(1 for _ in read_verified(repaired))
        replay_dir = recovery / "replay"
        replay_result = replay(repaired, replay_dir)
        replay_state = read_json(replay_dir / "STATE.json", {}) or {}

        open_positions = []
        for cohort, group in (replay_state.get("groups") or {}).items():
            for row in group.get("positions", []):
                if row.get("closed_ms") is None:
                    open_positions.append({
                        "cohort": cohort,
                        "id": row.get("id"),
                        "symbol": row.get("symbol"),
                        "venue": row.get("venue"),
                        "policy": row.get("policy"),
                        "scenario": row.get("scenario"),
                        "opened_ms": row.get("opened_ms"),
                        "censored_due_segment_gap": True,
                        "carried_forward": False,
                    })

        result.update(
            available=True,
            verified_inputs=verified,
            replay=replay_result,
            source_input_sha256=digest(source),
            repaired_input_sha256=digest(repaired),
            open_positions_censored=len(open_positions),
            positions=open_positions,
        )
    except Exception as exc:
        result.update(
            available=False,
            reason="RECOVERY_REPLAY_FAILED",
            error=str(exc)[:2000],
        )

    atomic(recovery / "RECOVERY.json", result, durable=True)
    return result


def current_segment(state):
    ident = state.get("current_segment")
    if not ident:
        return None
    return next(
        (row for row in state.get("segments", []) if row.get("id") == ident),
        None,
    )


def mark_orphan_after_restart(state_path, state):
    segment = current_segment(state)
    if not segment:
        return
    if segment.get("status") in (
        "COMPLETE",
        "FAILED",
        "INTERRUPTED",
        "CENSORED_GAP",
    ):
        state["current_segment"] = None
        write_state(state_path, state)
        return

    end = now_ms()
    start = int(
        state.get("last_manager_heartbeat_ms")
        or state.get("updated_ms")
        or end
    )
    start = min(start, end)
    gap = {
        "kind": "POWER_OR_MANAGER_GAP",
        "start_ms": start,
        "end_ms": end,
        "duration_ms": max(0, end - start),
        "segment": segment["id"],
        "missing_market_data": True,
        "continuous_experiment": False,
        "orders": False,
    }
    state["gaps"].append(gap)
    state["continuous_experiment"] = False
    segment["status"] = "CENSORED_GAP"
    segment["ended_ms"] = end
    segment["reason"] = "POWER_OR_MANAGER_RESTART"
    segment["recovery"] = salvage_segment(
        segment["session"],
        state["run_root"],
        segment["id"],
    )
    state["current_segment"] = None
    append_jsonl(Path(state["run_root"]) / "events.jsonl", gap, durable=True)
    write_state(state_path, state)


def start_segment(state_path, state):
    if len(state["segments"]) >= MAX_SEGMENTS:
        raise RuntimeError("A51R_MAX_SEGMENTS_REACHED")

    number = len(state["segments"]) + 1
    ident = f"seg-{number:02d}"
    session = BASE / "evidence" / (
        "a51r-9h-" + state["run_id"] + "-" + ident
    )
    if session.exists():
        raise RuntimeError("A51R_SEGMENT_PATH_ALREADY_EXISTS:" + str(session))

    log_dir = Path(state["run_root"]) / "segments"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (ident + "-supervisor.log")
    log_handle = log_path.open("a", buffering=1)

    env = clean_env()
    env.update(
        PYTHONPATH=str(ROOT),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUNBUFFERED="1",
        BELIAL_A1_NO_BROWSER="1",
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-m",
            "a51.supervisor",
            "--session",
            str(session),
        ],
        cwd=ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    now = now_ms()
    segment = {
        "id": ident,
        "session": str(session),
        "supervisor_pid": process.pid,
        "log": str(log_path),
        "status": "PREPARING",
        "started_manager_ms": now,
        "capture_started_ms": None,
        "ended_ms": None,
        "active_ms": 0,
        "primary_ready": False,
        "primary_gap_started_ms": None,
        "reason": None,
        "returncode": None,
        "recovery": None,
    }
    state["segments"].append(segment)
    state["current_segment"] = ident
    write_state(state_path, state)
    append_jsonl(
        Path(state["run_root"]) / "events.jsonl",
        {
            "kind": "SEGMENT_STARTED",
            "at_ms": now,
            "segment": ident,
            "session": str(session),
            "orders": False,
        },
        durable=True,
    )
    return process, log_handle, segment


def process_tree_metrics(root_pid):
    rows = {}
    for path in Path("/proc").glob("[0-9]*"):
        try:
            status = {}
            for line in (path / "status").read_text(errors="replace").splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    status[key] = value.strip()
            pid = int(path.name)
            ppid = int(status.get("PPid", "0"))
            rss = int(status.get("VmRSS", "0 kB").split()[0])
            raw = (path / "stat").read_text()
            end = raw.rfind(")")
            fields = raw[end + 2:].split()
            hz = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
            cpu = (int(fields[11]) + int(fields[12])) / hz
            rows[pid] = {
                "pid": pid,
                "ppid": ppid,
                "rss_kb": rss,
                "cpu_seconds": cpu,
            }
        except Exception:
            pass

    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, row in rows.items():
            if pid not in descendants and row["ppid"] in descendants:
                descendants.add(pid)
                changed = True

    processes = [rows[pid] for pid in descendants if pid in rows]
    return {
        "process_count": len(processes),
        "rss_kb": sum(row["rss_kb"] for row in processes),
        "cpu_seconds": sum(row["cpu_seconds"] for row in processes),
    }


def sample_performance(state, process):
    segment = current_segment(state)
    if not segment:
        return
    session = Path(segment["session"])
    books = read_json(session / "paper/books.json", {}) or {}
    ui = read_json(session / "ui/status.json", {}) or {}
    platform = read_json(session / "PLATFORM.json", {}) or {}
    satellite = read_json(session / "satellites/state.json", {}) or {}

    append_jsonl(
        Path(state["run_root"]) / "performance.jsonl",
        {
            "at_ms": now_ms(),
            "segment": segment["id"],
            **process_tree_metrics(process.pid),
            "messages_per_second": books.get("messages_per_second"),
            "rust_to_paper_ms_p95": books.get("rust_to_paper_ms_p95"),
            "ingest_queue": books.get("ingest_queue"),
            "ui_max_payload_bytes": ui.get("maximum_payload_bytes"),
            "ui_coalesced_payloads": ui.get("coalesced_payloads"),
            "coverage": platform.get("coverage"),
            "coverage_peak": platform.get("coverage_peak"),
            "satellite_state": satellite.get("state"),
            "satellite_venues": satellite.get("venues"),
            "primary_ready": primary_ready(platform),
            "orders": False,
        },
    )


def stop_child(process, timeout=190):
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGINT)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate(process, 15)


def finalize_report(state_path, state):
    run_root = Path(state["run_root"])
    perf = run_root / "performance.jsonl"
    samples = []
    if perf.exists():
        for line in perf.read_text().splitlines():
            if line.strip():
                with contextlib.suppress(Exception):
                    samples.append(json.loads(line))

    def numeric(key):
        return [
            float(row[key])
            for row in samples
            if isinstance(row.get(key), (int, float))
            and not isinstance(row.get(key), bool)
        ]

    def median(values):
        if not values:
            return None
        values = sorted(values)
        middle = len(values) // 2
        if len(values) % 2:
            return values[middle]
        return (values[middle - 1] + values[middle]) / 2

    # Sum CPU deltas within each segment instead of subtracting unrelated
    # process trees across restarts.
    cpu_by_segment = {}
    for row in samples:
        value = row.get("cpu_seconds")
        ident = row.get("segment")
        if ident and isinstance(value, (int, float)):
            cpu_by_segment.setdefault(ident, []).append(float(value))
    cpu_total = sum(
        max(0.0, values[-1] - values[0])
        for values in cpu_by_segment.values()
        if len(values) >= 2
    )

    rss = numeric("rss_kb")
    latency = numeric("rust_to_paper_ms_p95")
    payload = numeric("ui_max_payload_bytes")
    mps = numeric("messages_per_second")

    report = {
        "schema": "belial_a51r_resilient_9h_report_v1",
        "run_id": state["run_id"],
        "target_active_ms": state["target_active_ms"],
        "accumulated_active_ms": state["accumulated_active_ms"],
        "completed": state["completed"],
        "continuous_experiment": state["continuous_experiment"],
        "resume_mode": state["resume_mode"],
        "paper_position_carry_across_segment_gap": False,
        "transient_network_gap_may_preserve_open_paper_positions": True,
        "gap_count": len(state["gaps"]),
        "gaps": state["gaps"],
        "segments": state["segments"],
        "orders": False,
        "paper": True,
        "shadow": True,
        "performance": {
            "samples": len(samples),
            "rss_peak_mb": max(rss) / 1024 if rss else None,
            "rss_median_mb": median(rss) / 1024 if rss else None,
            "cpu_seconds_sum_of_segment_deltas": cpu_total,
            "messages_per_second_median": median(mps),
            "rust_to_paper_ms_p95_median": median(latency),
            "rust_to_paper_ms_p95_peak": max(latency) if latency else None,
            "ui_max_payload_bytes_peak": max(payload) if payload else None,
            "primary_ready_sample_ratio": (
                sum(row.get("primary_ready") is True for row in samples)
                / len(samples)
                if samples else None
            ),
        },
        "baseline_smoke25_reference": {
            "rss_peak_mb": 871.7890625,
            "rss_median_mb": 844.69140625,
            "cpu_seconds_delta_over_1500s": 793.5400000000001,
            "rust_to_paper_ms_p95_median": 8.972759,
            "ui_http_ms_p95": 34.359661000053165,
            "ui_max_payload_bytes_peak": 128977.0,
        },
    }
    report_path = run_root / "A51R_RESILIENT_9H_REPORT.json"
    atomic(report_path, report, durable=True)
    state["final_report"] = str(report_path)
    write_state(state_path, state)
    return report_path


def run(state_path):
    state_path = Path(state_path)
    runtime = state_path.parent
    runtime.mkdir(parents=True, exist_ok=True)
    lock = (runtime / "run-manager.lock").open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    state = read_json(state_path)
    if not isinstance(state, dict) or state.get("schema") != SCHEMA:
        raise RuntimeError("A51R_RUN_STATE_INVALID")
    if state.get("completed"):
        return 0
    if not state.get("active"):
        raise RuntimeError("A51R_RUN_NOT_ACTIVE")

    mark_orphan_after_restart(state_path, state)

    stop_requested = False

    def handle_stop(*_):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    process = None
    log_handle = None
    last_clock = time.monotonic()
    last_sync = 0
    last_perf = 0
    consecutive_failures = 0
    wall_deadline = int(state["created_ms"]) + MAX_WALL_MS

    try:
        while state["accumulated_active_ms"] < state["target_active_ms"]:
            now = now_ms()

            if now >= wall_deadline:
                if process:
                    stop_child(process)
                state["active"] = False
                state["completed"] = False
                state["terminal_reason"] = "MAX_24H_WALL_BUDGET_REACHED"
                state["last_manager_heartbeat_ms"] = now_ms()
                write_state(state_path, state)
                report = finalize_report(state_path, state)
                print("A51R_TERMINAL_INCOMPLETE=true", flush=True)
                print("A51R_REPORT=" + str(report), flush=True)
                return 4

            if stop_requested:
                if process:
                    stop_child(process)
                state["last_manager_heartbeat_ms"] = now_ms()
                write_state(state_path, state)
                return 0

            if process is None:
                process, log_handle, _ = start_segment(state_path, state)
                last_clock = time.monotonic()
                last_sync = now_ms()
                last_perf = 0
                continue

            segment = current_segment(state)
            session = Path(segment["session"])
            platform = read_json(session / "PLATFORM.json", {}) or {}
            session_state = read_json(session / "SESSION.json", {}) or {}
            integrity = read_json(session / "INTEGRITY.json", {"ok": True}) or {"ok": True}

            mono = time.monotonic()
            dt_ms = int(max(0.0, min(5.0, mono - last_clock)) * 1000)
            last_clock = mono
            now = now_ms()
            phase = session_state.get("phase")
            ready = phase == "CAPTURING" and primary_ready(platform)

            if phase == "CAPTURING" and segment["capture_started_ms"] is None:
                segment["capture_started_ms"] = now
                segment["status"] = "CAPTURING"

            if ready:
                state["accumulated_active_ms"] += dt_ms
                segment["active_ms"] += dt_ms
                if segment["primary_gap_started_ms"] is not None:
                    gap_start = segment["primary_gap_started_ms"]
                    gap = {
                        "kind": "PRIMARY_NETWORK_GAP",
                        "start_ms": gap_start,
                        "end_ms": now,
                        "duration_ms": now - gap_start,
                        "segment": segment["id"],
                        "missing_market_data": True,
                        "segment_restarted": False,
                        "continuous_experiment": False,
                        "orders": False,
                    }
                    state["gaps"].append(gap)
                    state["continuous_experiment"] = False
                    append_jsonl(
                        Path(state["run_root"]) / "events.jsonl",
                        gap,
                        durable=True,
                    )
                    segment["primary_gap_started_ms"] = None
                segment["primary_ready"] = True
                consecutive_failures = 0
            elif phase == "CAPTURING":
                segment["primary_ready"] = False
                if segment["primary_gap_started_ms"] is None:
                    segment["primary_gap_started_ms"] = now

            restart_reason = None
            if integrity.get("ok") is False:
                restart_reason = "SEGMENT_INTEGRITY_FAILED"
            elif (
                segment["primary_gap_started_ms"] is not None
                and now - segment["primary_gap_started_ms"]
                >= PRIMARY_GAP_RESTART_MS
            ):
                restart_reason = "PRIMARY_PATH_GAP_OVER_180S"
            elif (
                phase != "CAPTURING"
                and now - segment["started_manager_ms"]
                >= PREPARATION_TIMEOUT_MS
            ):
                restart_reason = "SEGMENT_PREPARATION_TIMEOUT"

            if state["accumulated_active_ms"] >= state["target_active_ms"]:
                segment["reason"] = "TARGET_PRIMARY_READY_TIME_REACHED"
                stop_child(process)
            elif restart_reason:
                # Long gap: do not carry PAPER positions through it. A graceful
                # segment finalization happens first; next segment starts fresh.
                segment["reason"] = restart_reason
                if segment["primary_gap_started_ms"] is not None:
                    gap = {
                        "kind": "PRIMARY_NETWORK_GAP",
                        "start_ms": segment["primary_gap_started_ms"],
                        "end_ms": now,
                        "duration_ms": now - segment["primary_gap_started_ms"],
                        "segment": segment["id"],
                        "missing_market_data": True,
                        "segment_restarted": True,
                        "continuous_experiment": False,
                        "orders": False,
                    }
                    state["gaps"].append(gap)
                    state["continuous_experiment"] = False
                    append_jsonl(
                        Path(state["run_root"]) / "events.jsonl",
                        gap,
                        durable=True,
                    )
                    segment["primary_gap_started_ms"] = None
                stop_child(process)

            if now - last_perf >= PERF_SAMPLE_MS and process.poll() is None:
                sample_performance(state, process)
                last_perf = now

            if now - last_sync >= STATE_SYNC_MS:
                state["last_manager_heartbeat_ms"] = now
                write_state(state_path, state)
                last_sync = now

            if process.poll() is not None:
                returncode = process.returncode
                if log_handle and not log_handle.closed:
                    log_handle.close()
                log_handle = None

                segment["returncode"] = returncode
                segment["ended_ms"] = now_ms()

                if state["accumulated_active_ms"] >= state["target_active_ms"]:
                    segment["status"] = "COMPLETE"
                    state["current_segment"] = None
                    state["completed"] = True
                    state["active"] = False
                    state["last_manager_heartbeat_ms"] = now_ms()
                    write_state(state_path, state)
                    report = finalize_report(state_path, state)
                    print("A51R_RESILIENT_9H_COMPLETE=true", flush=True)
                    print("A51R_RESILIENT_REPORT=" + str(report), flush=True)
                    return 0

                summary = read_json(session / "SUMMARY.json", {}) or {}
                if segment.get("reason"):
                    segment["status"] = "INTERRUPTED"
                elif summary.get("capture_started"):
                    segment["status"] = "INTERRUPTED"
                    segment["reason"] = "SUPERVISOR_ENDED_BEFORE_RUN_TARGET"
                else:
                    segment["status"] = "FAILED"
                    segment["reason"] = "SUPERVISOR_ENDED_DURING_PREPARATION"

                # If the supervisor could not produce its normal replay, audit
                # the durable prefix here. Never edit original segment bytes.
                if not (session / "REPLAY_CHECK.json").exists():
                    segment["recovery"] = salvage_segment(
                        session,
                        state["run_root"],
                        segment["id"],
                    )

                state["current_segment"] = None
                state["last_manager_heartbeat_ms"] = now_ms()
                write_state(state_path, state)
                process = None
                consecutive_failures += 1

                backoff = min(
                    300,
                    15 * (2 ** min(consecutive_failures - 1, 4)),
                )
                append_jsonl(
                    Path(state["run_root"]) / "events.jsonl",
                    {
                        "kind": "SEGMENT_RESTART_BACKOFF",
                        "at_ms": now_ms(),
                        "seconds": backoff,
                        "segment": segment["id"],
                        "reason": segment["reason"],
                        "orders": False,
                    },
                    durable=True,
                )
                deadline = time.monotonic() + backoff
                while time.monotonic() < deadline and not stop_requested:
                    time.sleep(1)
                continue

            time.sleep(1)

    finally:
        if process and process.poll() is None:
            stop_child(process)
        if log_handle and not log_handle.closed:
            log_handle.close()
        lock.close()

    return 0


def self_test():
    prefix, dropped = complete_prefix(b'{"a":1}\n{"b":2}')
    assert prefix == b'{"a":1}\n'
    assert dropped == len(b'{"b":2}')
    assert primary_ready({
        "integrity": {"ok": True},
        "coverage": {
            "live_signal_sources": 1,
            "usable_books": 1,
            "paper_prerequisites_ready": 1,
        },
    })
    assert not primary_ready({
        "integrity": {"ok": True},
        "coverage": {
            "live_signal_sources": 0,
            "usable_books": 1,
            "paper_prerequisites_ready": 1,
        },
    })
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        print("A51R_RUN_MANAGER_SELF_TEST=PASS")
        return 0
    if args.state is None:
        raise SystemExit("--state required")
    if args.init:
        if not args.run_id:
            raise SystemExit("--run-id required with --init")
        state = init_state(args.state, args.run_id)
        print("A51R_RUN_INITIALIZED=true")
        print("A51R_RUN_ROOT=" + state["run_root"])
        return 0
    return run(args.state)


if __name__ == "__main__":
    raise SystemExit(main())
