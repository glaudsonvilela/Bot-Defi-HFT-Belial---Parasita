"""A5.1R FIX1 boot/network recovery guard.

Patches only the expensive recovery hook in the already validated A5.1R run
manager. Crashed segments are marked for deferred streaming audit; they are not
copied or replayed before the next live segment starts.
"""
from __future__ import annotations

import argparse

from pathlib import Path

from a51.common import atomic, now_ms
import a51.run_manager as manager


def deferred_salvage(segment_path, run_root, segment_id):
    recovery_dir = Path(run_root) / "recovery-deferred" / segment_id
    recovery_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "schema": "belial_a51r_deferred_recovery_v1",
        "status": "DEFERRED_STREAM_AUDIT",
        "segment": segment_id,
        "source_session": str(segment_path),
        "scheduled_ms": now_ms(),
        "original_segment_untouched": True,
        "input_copy_created": False,
        "paper_position_carry_across_segment_gap": False,
        "orders": False,
    }
    atomic(recovery_dir / "RECOVERY_PENDING.json", result, durable=True)
    return result


def install_patch():
    manager.salvage_segment = deferred_salvage
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        assert install_patch()
        assert manager.salvage_segment is deferred_salvage
        print("A51R_RESUME_GUARD_SELF_TEST=PASS")
        return 0

    if args.state is None:
        raise SystemExit("--state required")

    install_patch()
    return manager.run(args.state)


if __name__ == "__main__":
    raise SystemExit(main())
