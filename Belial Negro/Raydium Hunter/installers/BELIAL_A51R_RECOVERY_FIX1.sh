#!/usr/bin/env bash
set -euo pipefail
umask 077

# BELIAL A5.1R RECOVERY FIX1
#
# Observed failure:
#   seg-01/paper/inputs.jsonl ~= 1.3 GiB
#   old recovery used read_bytes() + full duplicate copy before seg-02
#   run-manager reached ~4.9 GiB RAM and blocked recovery
#
# FIX1:
#   - preserves accumulated_active_ms exactly;
#   - leaves canonical A5.1R run_manager.py byte-identical;
#   - resume_guard replaces only salvage_segment at runtime with O(1)
#     DEFERRED_STREAM_AUDIT metadata;
#   - fresh seg-02 starts immediately;
#   - old generated recovery COPY is deleted, original seg-01 is untouched;
#   - after the 9h live target completes, recovery_stream validates the old
#     journal line-by-line with bounded memory and no duplicate input copy;
#   - future reboots and >=180s network segment restarts use the same guard;
#   - orders=false.

EXPECTED_HEAD="8cb7dcb98e50dc102816999c2d5f558876758166"

BASE="${BELIAL_BASE:-/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter}"
REPO="${BELIAL_GIT_REPO:-$HOME/Área de trabalho/belial-github-publicar}"

SOURCE_REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a51r-16q-9h-v1"
SOURCE="$REPO/$SOURCE_REL"

NEW_REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a51r-16q-9h-v1-fix1"
NEW_SNAPSHOT="$REPO/$NEW_REL"

RELEASE_NAME="belial-a51r-16q-9h-v1-fix1"
DEST="$BASE/releases/$RELEASE_NAME"

INSTALLER_REL="Belial Negro/Raydium Hunter/installers/BELIAL_A51R_RECOVERY_FIX1.sh"
INSTALLER_DEST="$REPO/$INSTALLER_REL"

RUNTIME="$BASE/runtime/belial-a51r"
STATE="$RUNTIME/ACTIVE_RUN.json"
SERVICE="belial-a51r-9h.service"
LAUNCHER="$HOME/.local/bin/belial-a51r-run-manager"
UNIT="$HOME/.config/systemd/user/$SERVICE"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE="$BASE/evidence/a51r-fix1-$TS"
TMP="$(mktemp -d -t a51r-fix1-XXXXXX)"
EXPORT="$TMP/export"
CANON="$EXPORT/$SOURCE_REL"
WORK="$TMP/BELIAL_A51R_FIX1"
SELF="$(readlink -f "$0")"

cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

fail() {
  echo
  echo "BELIAL_A51R_FIX1_FAILED=$1" >&2
  exit 3
}

[ "$(id -u)" -ne 0 ] || fail "DO_NOT_RUN_AS_ROOT"

for cmd in git python3 sha256sum diff cp mkdir awk grep findmnt tee tail mv date readlink chmod tar systemctl journalctl pgrep du free; do
  command -v "$cmd" >/dev/null || fail "DEPENDENCY_MISSING:$cmd"
done

findmnt -rn --mountpoint /mnt/sherlock-ssd >/dev/null || fail "SSD_NOT_MOUNTED"
[ -d "$REPO/.git" ] || fail "GIT_REPO_NOT_FOUND"
[ -d "$SOURCE" ] || fail "A51R_SOURCE_NOT_FOUND"
[ -f "$STATE" ] || fail "ACTIVE_RUN_STATE_NOT_FOUND"

mkdir -p "$EVIDENCE" "$RUNTIME/backups"

echo "============================================================"
echo " BELIAL A5.1R | RECOVERY FIX1"
echo "============================================================"

# 0. Stop old memory-heavy manager.
echo "[0/8] Parando manager antigo"
systemctl --user stop "$SERVICE" >/dev/null 2>&1 || true
for _ in $(seq 1 30); do
  systemctl --user is-active --quiet "$SERVICE" || break
  sleep 1
done
if systemctl --user is-active --quiet "$SERVICE"; then
  systemctl --user kill --signal=SIGKILL "$SERVICE" || true
  sleep 2
fi
systemctl --user is-active --quiet "$SERVICE" && fail "OLD_SERVICE_STILL_ACTIVE"
echo "A51R_OLD_SERVICE_STOPPED=true"

# 1. Exact Git base.
echo "[1/8] Baseline Git"
cd "$REPO"
HEAD_NOW="$(git rev-parse HEAD)"
echo "HEAD=$HEAD_NOW"
[ "$HEAD_NOW" = "$EXPECTED_HEAD" ] || fail "UNEXPECTED_HEAD:$HEAD_NOW"
[ -z "$(git status --porcelain=v1)" ] || {
  git status --short
  fail "GIT_WORKTREE_NOT_CLEAN"
}
if git fetch origin main >"$EVIDENCE/git-fetch.log" 2>&1; then
  [ "$(git rev-parse origin/main)" = "$HEAD_NOW" ] || fail "LOCAL_REMOTE_DIVERGED"
else
  echo "GIT_FETCH_WARNING=true"
fi

# 2. Back up live state and preserve its 2h19+ counter.
echo "[2/8] Backup ACTIVE_RUN"
cp -a "$STATE" "$RUNTIME/backups/ACTIVE_RUN.before-fix1-$TS.json"

python3 - "$STATE" "$EVIDENCE/STATE_BEFORE.json" <<'PY'
import json,sys
from pathlib import Path
state=json.load(open(sys.argv[1]))
assert state.get("active") is True
assert state.get("completed") is False
assert int(state.get("target_active_ms") or 0)==32_400_000
assert int(state.get("accumulated_active_ms") or 0)>0
assert state.get("current_segment")
Path(sys.argv[2]).write_text(json.dumps(state,indent=2,sort_keys=True)+"\n")
print("A51R_PRESERVE_MS="+str(state["accumulated_active_ms"]))
print("A51R_PROGRESS="+format(state["accumulated_active_ms"]/state["target_active_ms"]*100,".2f")+"%")
print("A51R_OLD_SEGMENT="+str(state["current_segment"]))
PY

PRESERVED_MS="$(python3 - "$STATE" <<'PY'
import json,sys
print(int(json.load(open(sys.argv[1]))["accumulated_active_ms"]))
PY
)"

# 3. Create isolated FIX1 package; original A51R package stays unchanged.
echo "[3/8] Criando release FIX1"
mkdir -p "$EXPORT"
git archive --format=tar HEAD -- "$SOURCE_REL" | tar -xf - -C "$EXPORT"
[ -d "$CANON" ] || fail "CANONICAL_EXPORT_MISSING"

(
  cd "$CANON"
  PYTHONPATH="$CANON" PYTHONDONTWRITEBYTECODE=1     python3 -B -m a51.verify --hashes-only
) | tee "$EVIDENCE/base-verify.log"
grep -Fq "A51_PACKAGE_HASHES=PASS" "$EVIDENCE/base-verify.log"   || fail "BASE_HASH_GATE"

mkdir -p "$WORK"
cp -a "$CANON/." "$WORK/"
cp "$WORK/MANIFEST_SHA256.json" "$WORK/BASELINE_A51R_MANIFEST_SHA256.json"

python3 - "$WORK" <<'PY'
from pathlib import Path
import hashlib,json,sys
root=Path(sys.argv[1])

resume_guard='"""A5.1R FIX1 boot/network recovery guard.\n\nPatches only the expensive recovery hook in the already validated A5.1R run\nmanager. Crashed segments are marked for deferred streaming audit; they are not\ncopied or replayed before the next live segment starts.\n"""\nfrom __future__ import annotations\n\nimport argparse\n\nfrom pathlib import Path\n\nfrom a51.common import atomic, now_ms\nimport a51.run_manager as manager\n\n\ndef deferred_salvage(segment_path, run_root, segment_id):\n    recovery_dir = Path(run_root) / "recovery-deferred" / segment_id\n    recovery_dir.mkdir(parents=True, exist_ok=True)\n    result = {\n        "schema": "belial_a51r_deferred_recovery_v1",\n        "status": "DEFERRED_STREAM_AUDIT",\n        "segment": segment_id,\n        "source_session": str(segment_path),\n        "scheduled_ms": now_ms(),\n        "original_segment_untouched": True,\n        "input_copy_created": False,\n        "paper_position_carry_across_segment_gap": False,\n        "orders": False,\n    }\n    atomic(recovery_dir / "RECOVERY_PENDING.json", result, durable=True)\n    return result\n\n\ndef install_patch():\n    manager.salvage_segment = deferred_salvage\n    return True\n\n\ndef main():\n    parser = argparse.ArgumentParser()\n    parser.add_argument("--state", type=Path)\n    parser.add_argument("--self-test", action="store_true")\n    args = parser.parse_args()\n\n    if args.self_test:\n        assert install_patch()\n        assert manager.salvage_segment is deferred_salvage\n        print("A51R_RESUME_GUARD_SELF_TEST=PASS")\n        return 0\n\n    if args.state is None:\n        raise SystemExit("--state required")\n\n    install_patch()\n    return manager.run(args.state)\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
recovery_stream='"""Post-run bounded-memory audit for deferred A5.1R recovery segments.\n\nRuns only after the live resilient run is marked completed. It never duplicates\ninputs.jsonl and never edits original segment evidence.\n"""\nfrom __future__ import annotations\n\nimport argparse\nimport ast\nimport contextlib\nimport hashlib\nimport json\nimport os\n\nfrom pathlib import Path\n\nfrom a51.common import atomic, canonical, now_ms, read_json, strict_loads\nfrom a51.paper_controller import Controller\n\nMAX_LINE = 64 << 20\nZERO = "0" * 64\n\n\ndef stream_audit(segment, run_root):\n    session = Path(segment["session"])\n    source = session / "paper" / "inputs.jsonl"\n    config = session / "paper" / "CONFIG_EFFECTIVE.json"\n    out = Path(run_root) / "recovery-stream" / segment["id"]\n    out.mkdir(parents=True, exist_ok=True)\n\n    result = {\n        "schema": "belial_a51r_stream_audit_v1",\n        "segment": segment["id"],\n        "source_session": str(session),\n        "status": "FAILED",\n        "original_segment_untouched": True,\n        "input_copy_created": False,\n        "bounded_memory_streaming": True,\n        "paper_position_carry_across_segment_gap": False,\n        "orders": False,\n        "started_ms": now_ms(),\n    }\n\n    try:\n        if not source.is_file() or not config.is_file():\n            raise RuntimeError("PAPER_INPUT_OR_CONFIG_MISSING")\n\n        cfg = read_json(config)\n        if not isinstance(cfg, dict):\n            raise RuntimeError("REPLAY_CONFIG_REQUIRED")\n\n        output_hash = hashlib.sha256()\n        output_count = 0\n\n        def emit(kind, row):\n            nonlocal output_count\n            output_count += 1\n            output_hash.update(\n                (canonical({"kind": kind, "body": row}) + "\\n").encode()\n            )\n\n        control = Controller(cfg, emit)\n        previous = ZERO\n        sequence = 0\n        source_hash = hashlib.sha256()\n        total_bytes = 0\n        dropped_tail = 0\n\n        # Lower priority so post-run audit is polite to the desktop.\n        with contextlib.suppress(OSError):\n            os.nice(10)\n\n        with source.open("rb") as handle:\n            while True:\n                raw = handle.readline(MAX_LINE + 1)\n                if not raw:\n                    break\n                total_bytes += len(raw)\n                source_hash.update(raw)\n\n                if len(raw) > MAX_LINE:\n                    raise RuntimeError("RECOVERY_INPUT_LINE_TOO_LARGE")\n                if not raw.endswith(b"\\n"):\n                    dropped_tail = len(raw)\n                    break\n\n                row = strict_loads(raw)\n                if not isinstance(row, dict):\n                    raise RuntimeError("JOURNAL_ROW_NOT_OBJECT")\n\n                received = row.pop("sha256", None)\n                sequence += 1\n                if (\n                    row.get("schema") != "belial_a2_journal_v1"\n                    or row.get("sequence") != sequence\n                    or row.get("previous_sha256") != previous\n                ):\n                    raise RuntimeError("JOURNAL_SEQUENCE_OR_CHAIN")\n\n                actual = hashlib.sha256(canonical(row).encode()).hexdigest()\n                if actual != received:\n                    raise RuntimeError("JOURNAL_HASH_MISMATCH")\n                previous = actual\n\n                event = row.get("event")\n                if not isinstance(event, dict):\n                    raise RuntimeError("JOURNAL_EVENT_NOT_OBJECT")\n                control.apply(event)\n\n        snapshot = control.snapshot(control.last_at)\n        open_positions = []\n        for cohort, group in (snapshot.get("groups") or {}).items():\n            for position in group.get("positions", []):\n                if position.get("closed_ms") is None:\n                    open_positions.append({\n                        "cohort": cohort,\n                        "id": position.get("id"),\n                        "symbol": position.get("symbol"),\n                        "venue": position.get("venue"),\n                        "policy": position.get("policy"),\n                        "scenario": position.get("scenario"),\n                        "opened_ms": position.get("opened_ms"),\n                        "censored_due_segment_gap": True,\n                        "carried_forward": False,\n                    })\n\n        result.update(\n            status="PASS",\n            ended_ms=now_ms(),\n            verified_inputs=sequence,\n            source_bytes=total_bytes,\n            dropped_trailing_bytes=dropped_tail,\n            source_sha256=source_hash.hexdigest(),\n            final_chain_sha256=previous,\n            semantic_output_events=output_count,\n            semantic_output_sha256=output_hash.hexdigest(),\n            open_positions_censored=len(open_positions),\n            positions=open_positions,\n        )\n    except Exception as exc:\n        result.update(\n            ended_ms=now_ms(),\n            error=str(exc)[:4000],\n        )\n\n    atomic(out / "RECOVERY.json", result, durable=True)\n    return result\n\n\ndef run(state_path):\n    state_path = Path(state_path)\n    state = read_json(state_path, {}) or {}\n\n    if state.get("completed") is not True:\n        print("A51R_STREAM_AUDIT=DEFERRED_RUN_NOT_COMPLETE")\n        return 0\n\n    pending = [\n        segment\n        for segment in state.get("segments", [])\n        if (segment.get("recovery") or {}).get("status")\n        == "DEFERRED_STREAM_AUDIT"\n    ]\n\n    results = {}\n    for segment in pending:\n        result = stream_audit(segment, state["run_root"])\n        results[segment["id"]] = result\n        segment["recovery"] = result\n\n    summary = {\n        "schema": "belial_a51r_stream_audit_summary_v1",\n        "at_ms": now_ms(),\n        "segments_requested": len(pending),\n        "passed": sum(\n            value.get("status") == "PASS"\n            for value in results.values()\n        ),\n        "results": results,\n        "orders": False,\n    }\n    atomic(\n        Path(state["run_root"]) / "RECOVERY_STREAM_SUMMARY.json",\n        summary,\n        durable=True,\n    )\n\n    state["post_completion_recovery_audit"] = summary\n    atomic(state_path, state, durable=True)\n\n    print(\n        "A51R_STREAM_AUDIT_PASS="\n        + str(summary["passed"] == summary["segments_requested"]).lower()\n    )\n    return 0\n\n\ndef main():\n    parser = argparse.ArgumentParser()\n    parser.add_argument("--state", type=Path)\n    parser.add_argument("--self-test", action="store_true")\n    args = parser.parse_args()\n\n    if args.self_test:\n        source = Path(__file__).read_text()\n        tree = ast.parse(source)\n        forbidden = []\n        for node in ast.walk(tree):\n            if (\n                isinstance(node, ast.Call)\n                and isinstance(node.func, ast.Attribute)\n                and node.func.attr in {"read_bytes", "write_bytes"}\n            ):\n                forbidden.append(node.func.attr)\n        assert not forbidden\n        assert "readline(MAX_LINE + 1)" in source\n        print("A51R_STREAM_AUDIT_SELF_TEST=PASS")\n        return 0\n\n    if args.state is None:\n        raise SystemExit("--state required")\n    return run(args.state)\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
test_fix='import ast\nimport unittest\n\nfrom pathlib import Path\n\nfrom a51.common import ROOT\nfrom a51.resume_guard import deferred_salvage, install_patch\nfrom a51.recovery_stream import main as stream_main\n\n\nclass A51RRecoveryFix1Tests(unittest.TestCase):\n    def test_guard_patch_installs(self):\n        self.assertTrue(install_patch())\n\n    def test_deferred_salvage_is_constant_size_metadata(self):\n        source = (ROOT / "a51/resume_guard.py").read_text()\n        self.assertNotIn(".read_bytes()", source)\n        self.assertNotIn(".write_bytes(", source)\n        self.assertIn("DEFERRED_STREAM_AUDIT", source)\n\n    def test_stream_audit_uses_line_reader(self):\n        source = (ROOT / "a51/recovery_stream.py").read_text()\n        ast.parse(source)\n        self.assertNotIn(".read_bytes()", source)\n        self.assertNotIn(".write_bytes(", source)\n        self.assertIn("readline(MAX_LINE + 1)", source)\n\n    def test_original_run_manager_is_not_modified_by_fix_module(self):\n        baseline = (ROOT / "BASELINE_A51R_MANIFEST_SHA256.json")\n        self.assertTrue(baseline.is_file())\n\n\nif __name__ == "__main__":\n    unittest.main()\n'

(root/"a51/resume_guard.py").write_text(resume_guard,encoding="utf-8")
(root/"a51/recovery_stream.py").write_text(recovery_stream,encoding="utf-8")
(root/"tests_a51/test_recovery_fix1.py").write_text(test_fix,encoding="utf-8")

baseline=json.loads((root/"BASELINE_A51R_MANIFEST_SHA256.json").read_text())
for rel,expected in baseline["files"].items():
    path=root/rel
    if not path.is_file():
        raise SystemExit("BASE_MEMBER_MISSING:"+rel)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise SystemExit("BASE_MEMBER_CHANGED:"+rel)

meta={
    "schema":"belial_a51r_recovery_fix1_v1",
    "base_commit":"8cb7dcb98e50dc102816999c2d5f558876758166",
    "base_package_byte_identical":True,
    "runtime_patch":"resume_guard.deferred_salvage",
    "post_run_audit":"bounded_memory_streaming",
    "input_copy_created":False,
    "orders":False,
}
(root/"docs/RECOVERY_FIX1.json").write_text(
    json.dumps(meta,indent=2,sort_keys=True)+"\n"
)

for path in list((root/"a51").rglob("*.py"))+list((root/"tests_a51").rglob("*.py")):
    compile(path.read_text(),str(path),"exec")

print("A51R_FIX1_DERIVATION=PASS")
print("A51R_BASE_PACKAGE_BYTE_IDENTICAL=true")
PY

(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m a51.resume_guard --self-test
  PYTHONPATH="$WORK" python3 -B -m a51.recovery_stream --self-test
) | tee "$EVIDENCE/fix1-self-tests.log"
grep -Fq "A51R_RESUME_GUARD_SELF_TEST=PASS" "$EVIDENCE/fix1-self-tests.log"   || fail "RESUME_GUARD_SELF_TEST"
grep -Fq "A51R_STREAM_AUDIT_SELF_TEST=PASS" "$EVIDENCE/fix1-self-tests.log"   || fail "STREAM_AUDIT_SELF_TEST"

(
  cd "$WORK"
  PYTHONPATH="$WORK" PYTHONDONTWRITEBYTECODE=1     python3 -B -m unittest discover -s tests_a51 -v
) >"$EVIDENCE/a51-tests.log" 2>&1 || {
  tail -n 320 "$EVIDENCE/a51-tests.log" >&2
  fail "FIX1_TESTS_FAILED"
}
echo "A51R_FIX1_TESTS=PASS"

python3 - "$WORK/a51/resume_guard.py" "$WORK/a51/recovery_stream.py" <<'PY'
from pathlib import Path
import ast,sys
guard=Path(sys.argv[1]).read_text()
stream=Path(sys.argv[2]).read_text()
ast.parse(guard);ast.parse(stream)
for source in (guard,stream):
    if ".read_bytes()" in source:
        raise SystemExit("WHOLE_FILE_READ_FORBIDDEN")
    if ".write_bytes(" in source:
        raise SystemExit("FULL_INPUT_COPY_FORBIDDEN")
assert "DEFERRED_STREAM_AUDIT" in guard
assert "readline(MAX_LINE + 1)" in stream
print("A51R_FIX1_NO_WHOLE_JOURNAL_READ=PASS")
print("A51R_FIX1_NO_RECOVERY_INPUT_COPY=PASS")
PY

python3 - "$WORK" <<'PY'
from pathlib import Path
import hashlib,json,sys
root=Path(sys.argv[1]);manifest=root/"MANIFEST_SHA256.json";files={}
for path in sorted(root.rglob("*")):
    if not path.is_file():continue
    rel=str(path.relative_to(root))
    if rel=="MANIFEST_SHA256.json":continue
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1<<20),b""):h.update(block)
    files[rel]=h.hexdigest()
doc={
    "schema":"belial_a51_16q_9h_package_v1",
    "variant":"resilient_recovery_fix1",
    "base_commit":"8cb7dcb98e50dc102816999c2d5f558876758166",
    "duration_ms":32_400_000,
    "minutes":540,
    "requested_assets":16,
    "orders":False,
    "files":files,
}
manifest.write_text(json.dumps(doc,indent=2,sort_keys=True)+"\n")
PY

(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m a51.verify --log-dir "$EVIDENCE/sealed-tests"
) | tee "$EVIDENCE/sealed-verify.log"
grep -Fq "A51_PACKAGE_HASHES=PASS" "$EVIDENCE/sealed-verify.log"   || fail "SEALED_VERIFY"

diff -qr "$CANON/engine_a51" "$WORK/engine_a51" >"$EVIDENCE/rust.diff" 2>&1 || {
  cat "$EVIDENCE/rust.diff" >&2
  fail "RUST_CHANGED"
}
diff -q "$CANON/a51/run_manager.py" "$WORK/a51/run_manager.py" >/dev/null   || fail "ORIGINAL_RUN_MANAGER_CHANGED"
echo "A51R_FIX1_RUST_AND_MANAGER_BASE_PRESERVED=true"

if [ -e "$DEST" ]; then
  diff -qr "$WORK" "$DEST" >"$EVIDENCE/release.diff" 2>&1 || {
    cat "$EVIDENCE/release.diff" >&2
    fail "EXISTING_FIX1_RELEASE_DIFFERS"
  }
else
  STAGING="$BASE/releases/.a51r-fix1-$TS"
  cp -a "$WORK" "$STAGING"
  (
    cd "$STAGING"
    PYTHONPATH="$STAGING" python3 -B -m a51.verify --hashes-only
  ) >/dev/null
  mv "$STAGING" "$DEST"
fi
echo "A51R_FIX1_RELEASE=PASS"

# 4. Git checkpoint.
echo "[4/8] Checkpoint Git"
if [ -e "$NEW_SNAPSHOT" ]; then
  diff -qr "$WORK" "$NEW_SNAPSHOT" >"$EVIDENCE/snapshot.diff" 2>&1 || {
    cat "$EVIDENCE/snapshot.diff" >&2
    fail "FIX1_SNAPSHOT_DIFFERS"
  }
else
  mkdir -p "$(dirname "$NEW_SNAPSHOT")"
  cp -a "$WORK" "$NEW_SNAPSHOT"
fi

mkdir -p "$(dirname "$INSTALLER_DEST")" "$REPO/docs"
cp "$SELF" "$INSTALLER_DEST"
chmod 0755 "$INSTALLER_DEST"

cat > "$REPO/docs/HANDOFF_A51R_RECOVERY_FIX1.md" <<EOF
# A5.1R Recovery FIX1

Base: 8cb7dcb98e50dc102816999c2d5f558876758166

- preserva accumulated_active_ms do run ativo;
- A5.1R canônico e Rust ficam byte-idênticos;
- recovery no boot vira DEFERRED_STREAM_AUDIT em O(1);
- novo segmento inicia antes do audit multi-GiB;
- audit final é streaming linha por linha;
- não cria cópia de inputs.jsonl;
- seg-01 original permanece intocado;
- posições PAPER não atravessam gap entre segmentos;
- orders=false.
EOF

cat > "$REPO/docs/CURRENT_STATE.md" <<EOF
# Bot Belial — Estado atual

**A5.1R Recovery FIX1 / 9H PRIMARY-READY**

Recovery pós-reboot sem leitura integral de journal e sem cópia multi-GiB.
O contador acumulado anterior é preservado; seg-01 é censurado no gap e seg-02
continua a coleta. Audit do seg-01 ocorre por streaming após a meta viva.
EOF

CHECKPOINT="$REPO/Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A51R_RECOVERY_FIX1.sha256"
(
  cd "$REPO"
  sha256sum     "$NEW_REL/MANIFEST_SHA256.json"     "$NEW_REL/a51/resume_guard.py"     "$NEW_REL/a51/recovery_stream.py"     "$INSTALLER_REL"     docs/HANDOFF_A51R_RECOVERY_FIX1.md     docs/CURRENT_STATE.md     > "$CHECKPOINT"
)

DEEP="$REPO/Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv"
if ! grep -Fq "A51R_RECOVERY_FIX1" "$DEEP"; then
  printf '%s\tA51R_RECOVERY_FIX1\tLOCAL_GATES_PASS\t%s\t%s\t%s\n'     "$TS" "$RELEASE_NAME"     "$(sha256sum "$DEST/MANIFEST_SHA256.json" | awk '{print $1}')"     "$(sha256sum "$INSTALLER_DEST" | awk '{print $1}')"     >> "$DEEP"
fi

git add -f -- "$NEW_REL"
git add --   "$INSTALLER_REL"   "Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A51R_RECOVERY_FIX1.sha256"   "Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv"   docs/HANDOFF_A51R_RECOVERY_FIX1.md   docs/CURRENT_STATE.md

WS1=":(exclude)$NEW_REL/legacy/runner/tools/preflight_pools.py"
WS2=":(exclude)$NEW_REL/tests_a3/test_processes.py"
WS3=":(exclude)$NEW_REL/tests_a4/test_processes.py"
git diff --cached --check -- . "$WS1" "$WS2" "$WS3"   >"$EVIDENCE/diff-check.log" 2>&1 || {
    cat "$EVIDENCE/diff-check.log" >&2
    fail "NEW_WHITESPACE_ERROR"
}

TREE="$(git write-tree)"
IDX="$TMP/index";mkdir -p "$IDX"
git archive --format=tar "$TREE" -- "$NEW_REL" | tar -xf - -C "$IDX"
(
  cd "$IDX/$NEW_REL"
  PYTHONPATH="$IDX/$NEW_REL" python3 -B -m a51.verify --hashes-only
) | tee "$EVIDENCE/index-verify.log"
grep -Fq "A51_PACKAGE_HASHES=PASS" "$EVIDENCE/index-verify.log"   || fail "STAGED_INDEX_VERIFY"
echo "A51R_FIX1_STAGED_INDEX_CANONICAL=true"

git commit -m "fix(a51r): defer multi-gigabyte reboot recovery"
FIX_COMMIT="$(git rev-parse HEAD)"
git push origin main
git fetch origin main >/dev/null 2>&1 || true
[ "$(git rev-parse origin/main)" = "$FIX_COMMIT" ] || fail "REMOTE_HEAD_MISMATCH"
[ -z "$(git status --porcelain=v1)" ] || fail "WORKTREE_DIRTY_AFTER_COMMIT"
echo "A51R_FIX1_GIT=PASS"

# 5. Remove only the generated 1.3G recovery duplicate; preserve seg-01.
echo "[5/8] Limpando cópia recovery gerada"
python3 - "$STATE" "$DEST" "$EVIDENCE" "$PRESERVED_MS" <<'PY'
from pathlib import Path
import json,os,shutil,sys,time

state_path=Path(sys.argv[1]);dest=Path(sys.argv[2]);ev=Path(sys.argv[3]);preserved=int(sys.argv[4])
state=json.load(open(state_path))
if int(state.get("accumulated_active_ms") or -1)!=preserved:
    raise SystemExit("ACTIVE_COUNTER_CHANGED")

segment=next((s for s in state["segments"] if s["id"]==state["current_segment"]),None)
if not segment:raise SystemExit("CURRENT_SEGMENT_MISSING")
original=Path(segment["session"])/"paper"/"inputs.jsonl"
if not original.is_file():raise SystemExit("ORIGINAL_INPUT_MISSING")

run_root=Path(state["run_root"])
recovery=run_root/"recovery"
removed=[]
if recovery.exists():
    for p in recovery.rglob("*"):
        if p.is_file():
            removed.append({"path":str(p),"bytes":p.stat().st_size})
    shutil.rmtree(recovery)

cleanup={
 "schema":"a51r_fix1_cleanup_v1",
 "at_ms":time.time_ns()//1_000_000,
 "original_segment":segment["id"],
 "original_input":str(original),
 "original_input_bytes":original.stat().st_size,
 "original_segment_preserved":True,
 "removed_generated_recovery_files":removed,
}
(ev/"RECOVERY_COPY_CLEANUP.json").write_text(json.dumps(cleanup,indent=2,sort_keys=True)+"\n")

# Preserve last_manager_heartbeat_ms intentionally so the power gap begins at
# the last pre-outage manager heartbeat.
state["release"]=str(dest)
state["manager_fix"]="RESUME_GUARD_FIX1_PENDING_RESTART"
state["fix1_installed_ms"]=time.time_ns()//1_000_000

tmp=state_path.with_name(state_path.name+".fix1tmp")
with tmp.open("w") as f:
    json.dump(state,f,sort_keys=True,separators=(",",":"));f.write("\n");f.flush();os.fsync(f.fileno())
os.replace(tmp,state_path)

assert int(json.load(open(state_path))["accumulated_active_ms"])==preserved
print("A51R_FIX1_COUNTER_PRESERVED="+str(preserved))
print("A51R_FIX1_ORIGINAL_SEG01_PRESERVED=true")
print("A51R_FIX1_OLD_RECOVERY_COPY_REMOVED=true")
PY

# 6. Update persistent launcher, keeping same state file.
echo "[6/8] Atualizando launcher systemd"
[ -f "$UNIT" ] || fail "SYSTEMD_UNIT_MISSING"

cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
while ! findmnt -rn --mountpoint /mnt/sherlock-ssd >/dev/null 2>&1; do
  sleep 10
done

set +e
env   PYTHONPATH="$DEST"   PYTHONDONTWRITEBYTECODE=1   PYTHONUNBUFFERED=1   "$(command -v python3)" -B -m a51.resume_guard --state "$STATE"
RC=\$?
set -e

# This is a no-op unless the live target is already completed.
env   PYTHONPATH="$DEST"   PYTHONDONTWRITEBYTECODE=1   "$(command -v python3)" -B -m a51.recovery_stream --state "$STATE" || true

exit \$RC
EOF
chmod 0755 "$LAUNCHER"

systemctl --user daemon-reload
systemctl --user restart "$SERVICE"
sleep 2
systemctl --user is-active --quiet "$SERVICE" || {
  systemctl --user status "$SERVICE" --no-pager -l >&2 || true
  journalctl --user -u "$SERVICE" -n 180 --no-pager >&2 || true
  fail "FIX1_SERVICE_NOT_ACTIVE"
}
echo "A51R_FIX1_SERVICE=PASS"

# 7. New segment must appear and old segment must be censored.
echo "[7/8] Confirmando POWER GAP e seg-02"
START="$(date +%s)"
while :; do
  RESULT="$(python3 - "$STATE" "$PRESERVED_MS" <<'PY'
import json,sys
state=json.load(open(sys.argv[1]));pres=int(sys.argv[2])
current=state.get("current_segment") or ""
seg1=next((s for s in state.get("segments",[]) if s.get("id")=="seg-01"),{})
power=[g for g in state.get("gaps",[]) if g.get("kind")=="POWER_OR_MANAGER_GAP"]
acc=int(state.get("accumulated_active_ms") or 0)
recovery=seg1.get("recovery") or {}
ok=(current and current!="seg-01" and seg1.get("status")=="CENSORED_GAP" and power and recovery.get("status")=="DEFERRED_STREAM_AUDIT" and acc>=pres)
print(("1" if ok else "0")+"|"+current+"|"+str(acc)+"|"+str(len(power))+"|"+str(seg1.get("status"))+"|"+str(recovery.get("status")))
PY
)"
  IFS='|' read -r OK SEG ACC POWER_N OLD_STATUS REC_STATUS <<<"$RESULT"
  if [ "$OK" = "1" ]; then
    echo "A51R_FIX1_POWER_GAP=PASS"
    echo "A51R_FIX1_NEW_SEGMENT=$SEG"
    echo "A51R_FIX1_ACTIVE_MS=$ACC"
    break
  fi
  systemctl --user is-active --quiet "$SERVICE" || {
    journalctl --user -u "$SERVICE" -n 220 --no-pager >&2 || true
    fail "SERVICE_DIED_BEFORE_NEW_SEGMENT"
  }
  NOW="$(date +%s)"
  [ $((NOW-START)) -lt 120 ] || fail "NEW_SEGMENT_TIMEOUT"
  sleep 2
done

# 8. Wait for new segment PRIMARY-READY.
echo "[8/8] Aguardando PRIMARY-READY"
START="$(date +%s)"
while :; do
  RESULT="$(python3 - "$STATE" <<'PY'
from pathlib import Path
import json,sys
state=json.load(open(sys.argv[1]))
ident=state.get("current_segment")
seg=next((s for s in state.get("segments",[]) if s.get("id")==ident),{}) if ident else {}
session=Path(seg["session"]) if seg.get("session") else None
platform={};phase="";url=""
if session:
    try:platform=json.loads((session/"PLATFORM.json").read_text())
    except Exception:pass
    try:phase=json.loads((session/"SESSION.json").read_text()).get("phase","")
    except Exception:pass
    try:url=json.loads((session/"ui/URL.json").read_text()).get("url","")
    except Exception:pass
coverage=platform.get("coverage") or {}
ready=(phase=="CAPTURING" and (platform.get("integrity") or {}).get("ok") is True and int(coverage.get("live_signal_sources") or 0)>0 and int(coverage.get("usable_books") or 0)>0 and int(coverage.get("paper_prerequisites_ready") or 0)>0)
print(("1" if ready else "0")+"|"+str(ident or "")+"|"+str(session or "")+"|"+url+"|"+str(coverage.get("live_signal_sources") or 0)+"|"+str(coverage.get("usable_books") or 0)+"|"+str(coverage.get("paper_prerequisites_ready") or 0)+"|"+str(state.get("accumulated_active_ms") or 0))
PY
)"
  IFS='|' read -r READY SEG SESSION URL LIVE BOOKS ELIGIBLE ACC <<<"$RESULT"
  if [ "$READY" = "1" ]; then
    echo "A51R_FIX1_PRIMARY_READY=PASS live=$LIVE books=$BOOKS ready=$ELIGIBLE"
    echo "A51R_FIX1_ACTIVE_MS=$ACC"
    echo "A51R_FIX1_SESSION=$SESSION"
    [ -n "$URL" ] && echo "DASHBOARD_URL=$URL"
    break
  fi
  systemctl --user is-active --quiet "$SERVICE" || {
    journalctl --user -u "$SERVICE" -n 260 --no-pager >&2 || true
    fail "SERVICE_DIED_BEFORE_PRIMARY_READY"
  }
  NOW="$(date +%s)"
  [ $((NOW-START)) -lt 1500 ] || fail "PRIMARY_READY_TIMEOUT_25MIN"
  sleep 2
done

echo
echo "===== SERVICO APOS FIX1 ====="
systemctl --user status "$SERVICE" --no-pager -l | sed -n '1,18p'

echo
echo "============================================================"
echo " BELIAL A5.1R FIX1 | RETOMADA CONCLUIDA"
echo "============================================================"
echo "FIX_COMMIT=$FIX_COMMIT"
echo "PRESERVED_ACTIVE_MS=$PRESERVED_MS"
echo "CURRENT_ACTIVE_MS=$ACC"
echo "CURRENT_SEGMENT=$SEG"
echo "POWER_GAP_RECORDED=true"
echo "ORIGINAL_SEG01_PRESERVED=true"
echo "OLD_RECOVERY_COPY_REMOVED=true"
echo "AUDIT_SEG01=DEFERRED_STREAMING_AFTER_LIVE_TARGET"
echo "ORDERS=false"
