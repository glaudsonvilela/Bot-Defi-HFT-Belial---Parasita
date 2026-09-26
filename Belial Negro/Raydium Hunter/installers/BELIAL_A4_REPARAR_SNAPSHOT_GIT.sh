#!/usr/bin/env bash
set -euo pipefail
umask 077

# BELIAL A4 - repair canonical Git snapshot
#
# Root cause already diagnosed:
# - MANIFEST_SHA256.json is identical in Git and SSD.
# - Pool/universe/engine files are identical in Git and SSD.
# - The Git snapshot is missing package-member validation *.log files that
#   are present in the original SSD release and are required by the manifest.
# - The repository-preparation path excluded *.log, producing an incomplete
#   package snapshot.
#
# This repair:
# 1) does NOT change strategy, pools, engine or tests;
# 2) does NOT change MANIFEST_SHA256.json;
# 3) copies only missing files that are listed in the manifest;
# 4) accepts a missing file only if its SSD SHA256 exactly matches the manifest;
# 5) runs the official A4 hash verifier after repair;
# 6) preserves this repair script + handoff in Git;
# 7) stages changes but DOES NOT commit or push automatically.
#
# PAPER / SHADOW ONLY.

REPO="${BELIAL_GIT_REPO:-$HOME/Área de trabalho/belial-github-publicar}"
REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a4-90m-v1-fix1"
SNAP="$REPO/$REL"

BASE="${BELIAL_BASE:-/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter}"
SSD="$BASE/releases/belial-a4-90m-v1-fix1"

INSTALLER_REL="Belial Negro/Raydium Hunter/installers/BELIAL_A4_REPARAR_SNAPSHOT_GIT.sh"
INSTALLER_DEST="$REPO/$INSTALLER_REL"
HANDOFF="$REPO/docs/HANDOFF_A4_SNAPSHOT_REPAIR.md"

EXPECTED_BASELINE_HEAD="77e63531e0d17280341cbbde212959bb9b9730ec"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$(mktemp -d -t belial-a4-snapshot-repair-XXXXXX)"
MISSING_LIST="$TMP/missing.txt"
RESULT_JSON="$TMP/result.json"
SELF="$(readlink -f "$0")"

cleanup() {
  rm -rf -- "$TMP"
}
trap cleanup EXIT

fail() {
  echo
  echo "BELIAL_A4_SNAPSHOT_REPAIR_FAILED=$1" >&2
  exit 3
}

if [ "$(id -u)" -eq 0 ]; then
  fail "DO_NOT_RUN_AS_ROOT"
fi

for cmd in git python3 sha256sum find cp mkdir awk grep sed date readlink tee; do
  command -v "$cmd" >/dev/null || fail "DEPENDENCY_MISSING:$cmd"
done

[ -d "$REPO/.git" ] || fail "GIT_REPO_NOT_FOUND:$REPO"
[ -d "$SNAP" ] || fail "GIT_SNAPSHOT_NOT_FOUND:$SNAP"
[ -f "$SNAP/MANIFEST_SHA256.json" ] || fail "GIT_MANIFEST_NOT_FOUND"
[ -d "$SSD" ] || fail "SSD_RELEASE_NOT_FOUND:$SSD"
[ -f "$SSD/MANIFEST_SHA256.json" ] || fail "SSD_MANIFEST_NOT_FOUND"

echo "============================================================"
echo " BELIAL A4 | REPARO SNAPSHOT GIT"
echo " PAPER / SHADOW | manifest-driven"
echo "============================================================"
echo "REPO=$REPO"
echo "SNAP=$SNAP"
echo "SSD=$SSD"
echo

echo "[0/6] Baseline Git"
cd "$REPO"

HEAD_NOW="$(git rev-parse HEAD)"
echo "HEAD=$HEAD_NOW"

if [ "$HEAD_NOW" != "$EXPECTED_BASELINE_HEAD" ]; then
  fail "UNEXPECTED_HEAD:$HEAD_NOW"
fi

if [ -n "$(git status --porcelain=v1)" ]; then
  echo "Working tree nao esta limpa:"
  git status --short
  fail "GIT_WORKTREE_NOT_CLEAN"
fi

if git fetch origin main >"$TMP/git-fetch.log" 2>&1; then
  REMOTE="$(git rev-parse origin/main)"
  echo "ORIGIN_MAIN=$REMOTE"
  [ "$REMOTE" = "$HEAD_NOW" ] || fail "LOCAL_REMOTE_DIVERGED"
else
  echo "GIT_FETCH_WARNING=true"
  cat "$TMP/git-fetch.log"
fi

echo
echo "[1/6] Confirmando que Git e SSD usam o MESMO manifesto"
GIT_MANIFEST_SHA="$(sha256sum "$SNAP/MANIFEST_SHA256.json" | awk '{print $1}')"
SSD_MANIFEST_SHA="$(sha256sum "$SSD/MANIFEST_SHA256.json" | awk '{print $1}')"

echo "GIT_MANIFEST_SHA256=$GIT_MANIFEST_SHA"
echo "SSD_MANIFEST_SHA256=$SSD_MANIFEST_SHA"

[ "$GIT_MANIFEST_SHA" = "$SSD_MANIFEST_SHA" ] || fail "MANIFESTS_DIFFER"

echo
echo "[2/6] Auditoria pre-reparo e copia SOMENTE de membros ausentes"

python3 - "$SNAP" "$SSD" "$MISSING_LIST" "$RESULT_JSON" <<'PY'
from pathlib import Path
import hashlib
import json
import shutil
import sys

git_root = Path(sys.argv[1])
ssd_root = Path(sys.argv[2])
missing_out = Path(sys.argv[3])
result_out = Path(sys.argv[4])

manifest_path = git_root / "MANIFEST_SHA256.json"
doc = json.loads(manifest_path.read_text(encoding="utf-8"))
files = doc.get("files")

if doc.get("schema") != "belial_a4_package_v1":
    raise SystemExit("UNEXPECTED_MANIFEST_SCHEMA")
if not isinstance(files, dict) or not files:
    raise SystemExit("MANIFEST_FILES_INVALID")

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

# Important: exclude ONLY the root manifest itself.
actual = {
    p.relative_to(git_root).as_posix()
    for p in git_root.rglob("*")
    if p.is_file() and p != manifest_path
}
expected = set(files)

extra = sorted(actual - expected)
missing = sorted(expected - actual)
bad = []

for rel in sorted(actual & expected):
    got = sha(git_root / rel)
    if got != files[rel]:
        bad.append({
            "path": rel,
            "expected": files[rel],
            "actual": got,
        })

print(f"EXPECTED_MEMBERS={len(expected)}")
print(f"ACTUAL_MEMBERS_BEFORE={len(actual)}")
print(f"MISSING_BEFORE={len(missing)}")
print(f"EXTRA_BEFORE={len(extra)}")
print(f"BAD_HASH_BEFORE={len(bad)}")

if extra:
    for rel in extra:
        print("UNEXPECTED_EXTRA=" + rel)
    raise SystemExit("UNEXPECTED_EXTRA_FILES")

if bad:
    for item in bad:
        print(
            "BAD_HASH="
            + item["path"]
            + " expected="
            + item["expected"]
            + " actual="
            + item["actual"]
        )
    raise SystemExit("EXISTING_FILE_HASH_MISMATCH")

if not missing:
    raise SystemExit("NO_MISSING_MEMBERS_TO_REPAIR")

# The diagnosed defect must be limited to validation log members.
allowed_prefixes = (
    "history_a1/validation/",
    "history_a2/validation/",
    "validation/",
)

unexpected_missing = [
    rel for rel in missing
    if not (rel.endswith(".log") and rel.startswith(allowed_prefixes))
]

if unexpected_missing:
    for rel in unexpected_missing:
        print("UNEXPECTED_MISSING_MEMBER=" + rel)
    raise SystemExit("MISSING_SET_NOT_LIMITED_TO_VALIDATION_LOGS")

# Verify all source files before changing the Git snapshot.
for rel in missing:
    src = ssd_root / rel
    if not src.is_file():
        raise SystemExit("SSD_SOURCE_MISSING:" + rel)
    got = sha(src)
    expected_hash = files[rel]
    if got != expected_hash:
        raise SystemExit(
            "SSD_SOURCE_HASH_MISMATCH:"
            + rel
            + ":expected="
            + expected_hash
            + ":actual="
            + got
        )

# All sources are proven. Now copy only the missing package members.
for rel in missing:
    src = ssd_root / rel
    dst = git_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print("RESTORED=" + rel)

missing_out.write_text(
    "".join(rel + "\n" for rel in missing),
    encoding="utf-8",
)

# Full post-copy verification against the unchanged manifest.
actual_after = {
    p.relative_to(git_root).as_posix()
    for p in git_root.rglob("*")
    if p.is_file() and p != manifest_path
}

if actual_after != expected:
    diff = sorted(actual_after.symmetric_difference(expected))
    raise SystemExit("POST_COPY_MEMBER_SET_MISMATCH:" + repr(diff[:50]))

bad_after = []
for rel in sorted(expected):
    got = sha(git_root / rel)
    if got != files[rel]:
        bad_after.append(rel)

if bad_after:
    raise SystemExit("POST_COPY_HASH_MISMATCH:" + repr(bad_after[:50]))

result = {
    "schema": "belial_a4_git_snapshot_repair_v1",
    "manifest_sha256": sha(manifest_path),
    "restored_count": len(missing),
    "restored": missing,
    "extra_before": extra,
    "bad_hash_before": bad,
    "post_member_set_exact": True,
    "post_hashes_exact": True,
    "strategy_changed": False,
    "pools_changed": False,
    "engine_changed": False,
    "tests_changed": False,
    "manifest_changed": False,
    "orders": False,
}
result_out.write_text(
    json.dumps(result, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

print("POST_MEMBER_SET_EXACT=true")
print("POST_HASHES_EXACT=true")
print("RESTORED_COUNT=" + str(len(missing)))
PY

echo
echo "[3/6] Rodando o verifier OFICIAL do A4 no snapshot reparado"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export BELIAL_A1_NO_BROWSER=1

(
  cd "$SNAP"
  PYTHONPATH="$SNAP" python3 -B -m a4.verify --hashes-only
) | tee "$TMP/a4-verify-hashes.log"

grep -Fq "A4_PACKAGE_HASHES=PASS" "$TMP/a4-verify-hashes.log" \
  || fail "OFFICIAL_A4_HASH_VERIFY_DID_NOT_PASS"

echo
echo "[4/6] Registrando causa e reparo para continuidade"

mkdir -p "$(dirname "$INSTALLER_DEST")" "$(dirname "$HANDOFF")"
cp -- "$SELF" "$INSTALLER_DEST"
chmod 0755 "$INSTALLER_DEST"

RESTORED_COUNT="$(wc -l < "$MISSING_LIST" | tr -d ' ')"
INSTALLER_SHA="$(sha256sum "$INSTALLER_DEST" | awk '{print $1}')"

cat > "$HANDOFF" <<EOF
# Bot Belial - A4 Git snapshot repair

UTC: $TS

## Root cause

The A4 FIX1 package manifest in Git and the original SSD release are identical:

\`\`\`text
MANIFEST_SHA256=$GIT_MANIFEST_SHA
\`\`\`

The Git snapshot was incomplete because the repository preparation flow excluded
\`*.log\` files while those validation logs are legitimate members of
\`MANIFEST_SHA256.json\`.

The verifier error used a symmetric difference and therefore the earlier
\`PACKAGE_MEMBERS_CHANGED\` message looked ambiguous. The complete diagnosis
proved these files were **missing**, not extra.

## What was NOT the cause

Hash comparison proved the following Git and SSD files were identical before
this repair:

- \`UNIVERSE_A2.json\`
- \`a4/catalog.py\`
- \`a4/spot_prepare.py\`
- \`a4/spot_validation.py\`
- \`a4/sensor_worker.py\`
- \`a4/venue_worker.py\`
- \`engine_a4/src/main.rs\`
- \`engine_a4/src/raydium_protocols.rs\`

Therefore this package-member failure was not caused by the pool expansion,
universe selection, strategy thresholds, sizing, or native engine code.

## Repair

Restored exactly **$RESTORED_COUNT** missing manifest members from:

\`\`\`text
$SSD
\`\`\`

Each file was copied only after its SHA256 matched the unchanged package
manifest.

No manifest entry was changed.

## Safety

- PAPER / SHADOW only.
- orders=false.
- strategy changed=false.
- pools changed=false.
- engine changed=false.
- tests changed=false.
- manifest changed=false.

## Official verification

\`\`\`text
A4_PACKAGE_HASHES=PASS
\`\`\`

## Repair installer

\`\`\`text
$INSTALLER_REL
SHA256=$INSTALLER_SHA
\`\`\`

After this checkpoint is committed, rerun
\`BELIAL_A4_90M_FIX4_ATUALIZAR.sh\`.
EOF

echo
echo "[5/6] Staging Git dos arquivos reparados"

# *.log is intentionally ignored globally, so these package members must be
# force-added because they are cryptographically required by this snapshot.
while IFS= read -r rel; do
  [ -n "$rel" ] || continue
  git add -f -- "$REL/$rel"
done < "$MISSING_LIST"

git add -- "$INSTALLER_REL" "docs/HANDOFF_A4_SNAPSHOT_REPAIR.md"

echo
echo "=== STAGED STATUS ==="
git status --short

echo
echo "=== STAGED DIFF CHECK ==="
git diff --cached --check

echo
echo "=== STAGED STAT ==="
git diff --cached --stat

echo
echo "[6/6] Final gate - staged content must not touch strategy/engine/tests/manifest"

python3 - "$REPO" "$REL" <<'PY'
from pathlib import Path
import subprocess
import sys

repo = Path(sys.argv[1])
relroot = sys.argv[2]

names = subprocess.check_output(
    ["git", "-C", str(repo), "diff", "--cached", "--name-only"],
    text=True,
).splitlines()

allowed_exact = {
    "Belial Negro/Raydium Hunter/installers/BELIAL_A4_REPARAR_SNAPSHOT_GIT.sh",
    "docs/HANDOFF_A4_SNAPSHOT_REPAIR.md",
}

bad = []
logs = []

for name in names:
    if name in allowed_exact:
        continue
    prefix = relroot + "/"
    if not name.startswith(prefix):
        bad.append(name)
        continue
    inner = name[len(prefix):]
    allowed_prefixes = (
        "history_a1/validation/",
        "history_a2/validation/",
        "validation/",
    )
    if inner.endswith(".log") and inner.startswith(allowed_prefixes):
        logs.append(inner)
    else:
        bad.append(name)

if bad:
    print("UNEXPECTED_STAGED_FILES:")
    for x in bad:
        print(x)
    raise SystemExit(1)

if not logs:
    raise SystemExit("NO_VALIDATION_LOGS_STAGED")

print("STAGED_VALIDATION_LOGS=" + str(len(logs)))
print("STAGED_SCOPE_EXACT=true")
PY

echo
echo "============================================================"
echo " BELIAL A4 SNAPSHOT REPAIR = PASS"
echo "============================================================"
echo "MANIFEST_CHANGED=false"
echo "POOLS_CHANGED=false"
echo "ENGINE_CHANGED=false"
echo "TESTS_CHANGED=false"
echo "ORDERS=false"
echo "RESTORED_COUNT=$RESTORED_COUNT"
echo
echo "COMMIT_REALIZADO=false"
echo "PUSH_REALIZADO=false"
echo
echo "Revise a saida e, somente depois, use:"
echo
echo "cd \"$REPO\""
echo "git commit -m \"fix(a4): restore manifest-complete canonical snapshot\""
echo "git push origin main"
echo
echo "Depois envie:"
echo "git status --short && git log -2 --oneline --decorate"
