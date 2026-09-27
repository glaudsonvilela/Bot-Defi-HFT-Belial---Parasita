"""Post-run bounded-memory audit for deferred A5.1R recovery segments.

Runs only after the live resilient run is marked completed. It never duplicates
inputs.jsonl and never edits original segment evidence.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import json
import os

from pathlib import Path

from a51.common import atomic, canonical, now_ms, read_json, strict_loads
from a51.paper_controller import Controller

MAX_LINE = 64 << 20
ZERO = "0" * 64


def stream_audit(segment, run_root):
    session = Path(segment["session"])
    source = session / "paper" / "inputs.jsonl"
    config = session / "paper" / "CONFIG_EFFECTIVE.json"
    out = Path(run_root) / "recovery-stream" / segment["id"]
    out.mkdir(parents=True, exist_ok=True)

    result = {
        "schema": "belial_a51r_stream_audit_v1",
        "segment": segment["id"],
        "source_session": str(session),
        "status": "FAILED",
        "original_segment_untouched": True,
        "input_copy_created": False,
        "bounded_memory_streaming": True,
        "paper_position_carry_across_segment_gap": False,
        "orders": False,
        "started_ms": now_ms(),
    }

    try:
        if not source.is_file() or not config.is_file():
            raise RuntimeError("PAPER_INPUT_OR_CONFIG_MISSING")

        cfg = read_json(config)
        if not isinstance(cfg, dict):
            raise RuntimeError("REPLAY_CONFIG_REQUIRED")

        output_hash = hashlib.sha256()
        output_count = 0

        def emit(kind, row):
            nonlocal output_count
            output_count += 1
            output_hash.update(
                (canonical({"kind": kind, "body": row}) + "\n").encode()
            )

        control = Controller(cfg, emit)
        previous = ZERO
        sequence = 0
        source_hash = hashlib.sha256()
        total_bytes = 0
        dropped_tail = 0

        # Lower priority so post-run audit is polite to the desktop.
        with contextlib.suppress(OSError):
            os.nice(10)

        with source.open("rb") as handle:
            while True:
                raw = handle.readline(MAX_LINE + 1)
                if not raw:
                    break
                total_bytes += len(raw)
                source_hash.update(raw)

                if len(raw) > MAX_LINE:
                    raise RuntimeError("RECOVERY_INPUT_LINE_TOO_LARGE")
                if not raw.endswith(b"\n"):
                    dropped_tail = len(raw)
                    break

                row = strict_loads(raw)
                if not isinstance(row, dict):
                    raise RuntimeError("JOURNAL_ROW_NOT_OBJECT")

                received = row.pop("sha256", None)
                sequence += 1
                if (
                    row.get("schema") != "belial_a2_journal_v1"
                    or row.get("sequence") != sequence
                    or row.get("previous_sha256") != previous
                ):
                    raise RuntimeError("JOURNAL_SEQUENCE_OR_CHAIN")

                actual = hashlib.sha256(canonical(row).encode()).hexdigest()
                if actual != received:
                    raise RuntimeError("JOURNAL_HASH_MISMATCH")
                previous = actual

                event = row.get("event")
                if not isinstance(event, dict):
                    raise RuntimeError("JOURNAL_EVENT_NOT_OBJECT")
                control.apply(event)

        snapshot = control.snapshot(control.last_at)
        open_positions = []
        for cohort, group in (snapshot.get("groups") or {}).items():
            for position in group.get("positions", []):
                if position.get("closed_ms") is None:
                    open_positions.append({
                        "cohort": cohort,
                        "id": position.get("id"),
                        "symbol": position.get("symbol"),
                        "venue": position.get("venue"),
                        "policy": position.get("policy"),
                        "scenario": position.get("scenario"),
                        "opened_ms": position.get("opened_ms"),
                        "censored_due_segment_gap": True,
                        "carried_forward": False,
                    })

        result.update(
            status="PASS",
            ended_ms=now_ms(),
            verified_inputs=sequence,
            source_bytes=total_bytes,
            dropped_trailing_bytes=dropped_tail,
            source_sha256=source_hash.hexdigest(),
            final_chain_sha256=previous,
            semantic_output_events=output_count,
            semantic_output_sha256=output_hash.hexdigest(),
            open_positions_censored=len(open_positions),
            positions=open_positions,
        )
    except Exception as exc:
        result.update(
            ended_ms=now_ms(),
            error=str(exc)[:4000],
        )

    atomic(out / "RECOVERY.json", result, durable=True)
    return result


def run(state_path):
    state_path = Path(state_path)
    state = read_json(state_path, {}) or {}

    if state.get("completed") is not True:
        print("A51R_STREAM_AUDIT=DEFERRED_RUN_NOT_COMPLETE")
        return 0

    pending = [
        segment
        for segment in state.get("segments", [])
        if (segment.get("recovery") or {}).get("status")
        == "DEFERRED_STREAM_AUDIT"
    ]

    results = {}
    for segment in pending:
        result = stream_audit(segment, state["run_root"])
        results[segment["id"]] = result
        segment["recovery"] = result

    summary = {
        "schema": "belial_a51r_stream_audit_summary_v1",
        "at_ms": now_ms(),
        "segments_requested": len(pending),
        "passed": sum(
            value.get("status") == "PASS"
            for value in results.values()
        ),
        "results": results,
        "orders": False,
    }
    atomic(
        Path(state["run_root"]) / "RECOVERY_STREAM_SUMMARY.json",
        summary,
        durable=True,
    )

    state["post_completion_recovery_audit"] = summary
    atomic(state_path, state, durable=True)

    print(
        "A51R_STREAM_AUDIT_PASS="
        + str(summary["passed"] == summary["segments_requested"]).lower()
    )
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        source = Path(__file__).read_text()
        tree = ast.parse(source)
        forbidden = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"read_bytes", "write_bytes"}
            ):
                forbidden.append(node.func.attr)
        assert not forbidden
        assert "readline(MAX_LINE + 1)" in source
        print("A51R_STREAM_AUDIT_SELF_TEST=PASS")
        return 0

    if args.state is None:
        raise SystemExit("--state required")
    return run(args.state)


if __name__ == "__main__":
    raise SystemExit(main())
