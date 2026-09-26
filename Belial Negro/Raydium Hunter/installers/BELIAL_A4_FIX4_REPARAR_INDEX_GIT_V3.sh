#!/usr/bin/env bash
set -euo pipefail
umask 077

# BELIAL A4 FIX4 - REPARO DO INDEX/GIT V3
#
# Diagnostico corrigido:
# - o snapshot FISICO belial-a4-90m-v1-fix4 ja possui todos os 318 membros;
# - os hashes fisicos batem com o MANIFEST_SHA256.json;
# - porem 26 logs canonicos estao ignorados por *.log e NAO estao no HEAD;
# - por isso `git archive HEAD` produz um pacote incompleto.
#
# Este script compara MANIFEST x ARVORE RASTREADA DO HEAD, nao apenas disco.
# Ele NAO copia nem altera os 26 logs: apenas prova seus hashes e usa git add -f.
#
# Depois, cria uma arvore Git temporaria a partir do INDEX staged (`git write-tree`),
# exporta essa arvore com `git archive` e executa o verifier oficial A4 nela.
#
# Nao faz commit/push.
# PAPER / SHADOW somente.

EXPECTED_HEAD="69fe351269639abb6a770a38bfe66c53a21f0eb3"

REPO="${BELIAL_GIT_REPO:-$HOME/Área de trabalho/belial-github-publicar}"
BASE="${BELIAL_BASE:-/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter}"

REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a4-90m-v1-fix4"
SNAP="$REPO/$REL"
SSD="$BASE/releases/belial-a4-90m-v1-fix4"

INSTALLER_REL="Belial Negro/Raydium Hunter/installers/BELIAL_A4_FIX4_REPARAR_INDEX_GIT_V3.sh"
INSTALLER_DEST="$REPO/$INSTALLER_REL"
HANDOFF_REL="docs/HANDOFF_A4_FIX4_INDEX_REPAIR_V3.md"
HANDOFF="$REPO/$HANDOFF_REL"
DEEP_LOG_REL="Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv"
DEEP_LOG="$REPO/$DEEP_LOG_REL"
CHECKPOINT_REL="Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A4_FIX4_INDEX_REPAIR_V3.sha256"
CHECKPOINT="$REPO/$CHECKPOINT_REL"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$(mktemp -d -t belial-a4-fix4-index-repair-XXXXXX)"
MISSING="$TMP/missing-from-head.txt"
SELF="$(readlink -f "$0")"

cleanup(){ rm -rf -- "$TMP"; }
trap cleanup EXIT

fail(){
  echo
  echo "BELIAL_A4_FIX4_INDEX_REPAIR_FAILED=$1" >&2
  exit 3
}

if [ "$(id -u)" -eq 0 ]; then
  fail "DO_NOT_RUN_AS_ROOT"
fi

for cmd in git python3 sha256sum tar cp mkdir grep awk date readlink tee wc; do
  command -v "$cmd" >/dev/null || fail "DEPENDENCY_MISSING:$cmd"
done

[ -d "$REPO/.git" ] || fail "GIT_REPO_NOT_FOUND:$REPO"
[ -d "$SNAP" ] || fail "GIT_SNAPSHOT_NOT_FOUND:$SNAP"
[ -f "$SNAP/MANIFEST_SHA256.json" ] || fail "GIT_MANIFEST_NOT_FOUND"
[ -d "$SSD" ] || fail "SSD_RELEASE_NOT_FOUND:$SSD"
[ -f "$SSD/MANIFEST_SHA256.json" ] || fail "SSD_MANIFEST_NOT_FOUND"

echo "============================================================"
echo " BELIAL A4 FIX4 | INDEX/GIT REPAIR V3"
echo "============================================================"
echo "REPO=$REPO"
echo "SNAP=$SNAP"
echo "SSD=$SSD"
echo

echo "[0/7] Baseline Git"
cd "$REPO"

HEAD_NOW="$(git rev-parse HEAD)"
echo "HEAD=$HEAD_NOW"
[ "$HEAD_NOW" = "$EXPECTED_HEAD" ] || fail "UNEXPECTED_HEAD:$HEAD_NOW"

# Ignored files do not appear here; any visible change is unrelated and unsafe.
if [ -n "$(git status --porcelain=v1)" ]; then
  echo "Working tree possui alteracoes nao relacionadas:"
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
echo "[1/7] Manifesto Git x SSD"

GIT_MANIFEST_SHA="$(sha256sum "$SNAP/MANIFEST_SHA256.json" | awk '{print $1}')"
SSD_MANIFEST_SHA="$(sha256sum "$SSD/MANIFEST_SHA256.json" | awk '{print $1}')"

echo "GIT_MANIFEST_SHA256=$GIT_MANIFEST_SHA"
echo "SSD_MANIFEST_SHA256=$SSD_MANIFEST_SHA"

[ "$GIT_MANIFEST_SHA" = "$SSD_MANIFEST_SHA" ] || fail "MANIFESTS_DIFFER"

echo
echo "[2/7] Prova: disco completo, HEAD incompleto"

python3 - "$REPO" "$REL" "$SNAP" "$SSD" "$MISSING" <<'PY'
from pathlib import Path
import hashlib, json, subprocess, sys

repo=Path(sys.argv[1])
relroot=sys.argv[2]
snap=Path(sys.argv[3])
ssd=Path(sys.argv[4])
missing_out=Path(sys.argv[5])

manifest_path=snap/"MANIFEST_SHA256.json"
doc=json.loads(manifest_path.read_text(encoding="utf-8"))
files=doc.get("files")

if doc.get("schema")!="belial_a4_package_v1":
    raise SystemExit("UNEXPECTED_MANIFEST_SCHEMA")
if not isinstance(files,dict) or not files:
    raise SystemExit("INVALID_MANIFEST_FILES")

expected=set(files)

def sha(p:Path)->str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):
            h.update(b)
    return h.hexdigest()

# 1) Estado fisico do snapshot.
physical={
    p.relative_to(snap).as_posix()
    for p in snap.rglob("*")
    if p.is_file() and p != manifest_path
}
physical_missing=sorted(expected-physical)
physical_extra=sorted(physical-expected)
physical_bad=[
    rel for rel in sorted(expected & physical)
    if sha(snap/rel)!=files[rel]
]

print(f"EXPECTED_MEMBERS={len(expected)}")
print(f"PHYSICAL_MEMBERS={len(physical)}")
print(f"PHYSICAL_MISSING={len(physical_missing)}")
print(f"PHYSICAL_EXTRA={len(physical_extra)}")
print(f"PHYSICAL_BAD_HASH={len(physical_bad)}")

if physical_missing or physical_extra or physical_bad:
    for x in physical_missing:print("PHYSICAL_MISSING_MEMBER="+x)
    for x in physical_extra:print("PHYSICAL_EXTRA_MEMBER="+x)
    for x in physical_bad:print("PHYSICAL_BAD_HASH_MEMBER="+x)
    raise SystemExit("PHYSICAL_SNAPSHOT_NOT_CANONICAL")

# 2) Estado realmente rastreado pelo HEAD.
tracked_full=subprocess.check_output(
    ["git","-C",str(repo),"ls-tree","-r","--name-only","HEAD","--",relroot],
    text=True,
).splitlines()

prefix=relroot+"/"
tracked={
    name[len(prefix):]
    for name in tracked_full
    if name.startswith(prefix) and name != relroot+"/MANIFEST_SHA256.json"
}

head_missing=sorted(expected-tracked)
head_extra=sorted(tracked-expected)

print(f"HEAD_TRACKED_MEMBERS={len(tracked)}")
print(f"HEAD_MISSING={len(head_missing)}")
print(f"HEAD_EXTRA={len(head_extra)}")

if head_extra:
    for x in head_extra:print("HEAD_UNEXPECTED_EXTRA="+x)
    raise SystemExit("HEAD_HAS_UNEXPECTED_PACKAGE_MEMBERS")

allowed_prefixes=(
    "history_a1/validation/",
    "history_a2/validation/",
    "validation/",
)
bad_missing=[
    rel for rel in head_missing
    if not (rel.endswith(".log") and rel.startswith(allowed_prefixes))
]
if bad_missing:
    for rel in bad_missing:print("HEAD_UNEXPECTED_MISSING="+rel)
    raise SystemExit("HEAD_MISSING_OUTSIDE_VALIDATION_LOGS")

if len(head_missing)!=26:
    for rel in head_missing:print("HEAD_MISSING_MEMBER="+rel)
    raise SystemExit("EXPECTED_26_HEAD_MISSING_LOGS_GOT_"+str(len(head_missing)))

# 3) Cada membro ausente no HEAD deve existir fisicamente, bater com manifesto,
#    bater com a release imutavel e estar ignorado pelo Git.
for rel in head_missing:
    local=snap/rel
    origin=ssd/rel

    if not local.is_file():
        raise SystemExit("LOCAL_MEMBER_MISSING:"+rel)
    if not origin.is_file():
        raise SystemExit("SSD_MEMBER_MISSING:"+rel)

    local_sha=sha(local)
    ssd_sha=sha(origin)
    expected_sha=files[rel]

    if local_sha!=expected_sha:
        raise SystemExit("LOCAL_HASH_MISMATCH:"+rel)
    if ssd_sha!=expected_sha:
        raise SystemExit("SSD_HASH_MISMATCH:"+rel)
    if local_sha!=ssd_sha:
        raise SystemExit("LOCAL_SSD_HASH_DIFFER:"+rel)

    full=relroot+"/"+rel
    ignored=subprocess.run(
        ["git","-C",str(repo),"check-ignore","-q","--",full]
    ).returncode==0
    if not ignored:
        raise SystemExit("EXPECTED_IGNORED_LOG_NOT_IGNORED:"+rel)

    print("HEAD_MISSING_BUT_LOCAL_VALID="+rel)

missing_out.write_text("".join(x+"\n" for x in head_missing),encoding="utf-8")

print("ROOT_CAUSE=IGNORED_CANONICAL_LOGS_NOT_STAGED_IN_NEW_FIX4_SNAPSHOT")
print("PHYSICAL_SNAPSHOT_CANONICAL=true")
print("HEAD_SNAPSHOT_CANONICAL=false")
print("HEAD_MISSING_VALIDATION_LOGS=26")
PY

echo
echo "[3/7] Verifier oficial sobre o snapshot fisico"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export BELIAL_A1_NO_BROWSER=1

(
  cd "$SNAP"
  PYTHONPATH="$SNAP" python3 -B -m a4.verify --hashes-only
) | tee "$TMP/physical-a4-hashes.log"

grep -Fq "A4_PACKAGE_HASHES=PASS" "$TMP/physical-a4-hashes.log" \
  || fail "PHYSICAL_A4_HASH_GATE_FAILED"

echo
echo "[4/7] Force-stage somente os 26 membros canonicos ausentes"

cd "$REPO"

while IFS= read -r rel; do
  [ -n "$rel" ] || continue
  git add -f -- "$REL/$rel"
done < "$MISSING"

echo "STAGED_LOGS_AFTER_FORCE_ADD=$(git diff --cached --name-only -- "$REL" | grep -c '\.log$' || true)"

echo
echo "[5/7] Prova do INDEX que sera commitado"

# `git write-tree` cria um objeto de arvore a partir do INDEX sem mover branch
# e sem criar commit. Assim validamos exatamente o conteudo staged.
TREE_SHA="$(git write-tree)"
echo "STAGED_TREE_SHA=$TREE_SHA"

INDEX_EXPORT="$TMP/index-export"
mkdir -p "$INDEX_EXPORT"

git archive --format=tar "$TREE_SHA" -- "$REL" | tar -xf - -C "$INDEX_EXPORT"

INDEX_SNAPSHOT="$INDEX_EXPORT/$REL"
[ -d "$INDEX_SNAPSHOT" ] || fail "INDEX_EXPORT_SNAPSHOT_MISSING"

(
  cd "$INDEX_SNAPSHOT"
  PYTHONPATH="$INDEX_SNAPSHOT" python3 -B -m a4.verify --hashes-only
) | tee "$TMP/index-a4-hashes.log"

grep -Fq "A4_PACKAGE_HASHES=PASS" "$TMP/index-a4-hashes.log" \
  || fail "STAGED_INDEX_A4_HASH_GATE_FAILED"

echo "STAGED_INDEX_A4_CANONICAL=true"

echo
echo "[6/7] Handoff, deep log e checkpoint"

mkdir -p "$(dirname "$INSTALLER_DEST")" "$(dirname "$HANDOFF")"
cp -- "$SELF" "$INSTALLER_DEST"
chmod 0755 "$INSTALLER_DEST"

INSTALLER_SHA="$(sha256sum "$INSTALLER_DEST" | awk '{print $1}')"

cat > "$HANDOFF" <<EOF
# Bot Belial - A4 FIX4 Index/Git Repair V3

UTC: $TS

## Causa confirmada

O snapshot físico A4 FIX4 já estava completo e válido:

\`\`\`text
expected_members=318
physical_members=318
physical_missing=0
physical_extra=0
physical_bad_hash=0
A4_PACKAGE_HASHES=PASS
\`\`\`

O defeito estava somente na árvore rastreada pelo commit **69fe351**.

Os 26 logs de validação são membros legítimos do
\`MANIFEST_SHA256.json\`, mas o repositório possui \`*.log\` no
\`.gitignore\`. Como o snapshot FIX4 era um caminho novo, o fechamento usou
\`git add\` normal e esses 26 arquivos ficaram fora do commit.

## Prova do HEAD

\`\`\`text
HEAD=$EXPECTED_HEAD
HEAD_MISSING_VALIDATION_LOGS=26
ROOT_CAUSE=IGNORED_CANONICAL_LOGS_NOT_STAGED_IN_NEW_FIX4_SNAPSHOT
\`\`\`

Cada um dos 26 arquivos:

1. já existia fisicamente no snapshot FIX4;
2. batia com o SHA256 do manifesto;
3. batia byte-a-byte com a release imutável do SSD;
4. estava ignorado pelo Git.

## Reparo

Foi usado \`git add -f\` somente nesses 26 membros canônicos.

Antes de qualquer commit, o INDEX staged foi convertido em uma árvore temporária
com \`git write-tree\`, exportado com \`git archive\` e validado pelo verifier
oficial A4.

\`\`\`text
STAGED_INDEX_A4_CANONICAL=true
A4_PACKAGE_HASHES=PASS
\`\`\`

## Nao alterado

- MANIFEST_SHA256.json;
- engine_a4;
- pools/universo;
- estratégia;
- testes;
- thresholds;
- sizing;
- políticas de saída;
- PAPER / SHADOW;
- orders=false.

## A5

O A5 bootstrap recusou corretamente o commit incompleto.

Depois do commit/push deste reparo, o bootstrap A5 deve ser reancorado no novo
HEAD. Não reutilizar o EXPECTED_HEAD 69fe351.
EOF

if [ ! -f "$DEEP_LOG" ]; then
  printf 'utc\tphase\tstatus\trelease\tmanifest_sha256\tinstaller_sha256\n' > "$DEEP_LOG"
fi

if ! grep -Fq "A4_FIX4_INDEX_REPAIR_V3" "$DEEP_LOG"; then
  printf '%s\tA4_FIX4_INDEX_REPAIR_V3\tSTAGED_INDEX_VERIFIED\tbelial-a4-90m-v1-fix4\t%s\t%s\n' \
    "$TS" "$GIT_MANIFEST_SHA" "$INSTALLER_SHA" >> "$DEEP_LOG"
fi

: > "$CHECKPOINT"
(
  cd "$REPO"
  sha256sum "$REL/MANIFEST_SHA256.json" >> "$CHECKPOINT"
  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    sha256sum "$REL/$rel" >> "$CHECKPOINT"
  done < "$MISSING"
  sha256sum \
    "$INSTALLER_REL" \
    "$HANDOFF_REL" \
    "$DEEP_LOG_REL" \
    >> "$CHECKPOINT"
)

git add -- "$INSTALLER_REL" "$HANDOFF_REL" "$DEEP_LOG_REL" "$CHECKPOINT_REL"

git diff --cached --check

echo
echo "=== STAGED STATUS ==="
git status --short

echo
echo "=== STAGED STAT ==="
git --no-pager diff --cached --stat

echo
echo "[7/7] Gate final de escopo"

python3 - "$REPO" "$REL" "$MISSING" <<'PY'
from pathlib import Path
import subprocess,sys

repo=Path(sys.argv[1])
relroot=sys.argv[2]
missing_file=Path(sys.argv[3])

expected_logs=set(missing_file.read_text().splitlines())
names=subprocess.check_output(
    ["git","-C",str(repo),"diff","--cached","--name-only"],
    text=True
).splitlines()

allowed_exact={
 "Belial Negro/Raydium Hunter/installers/BELIAL_A4_FIX4_REPARAR_INDEX_GIT_V3.sh",
 "docs/HANDOFF_A4_FIX4_INDEX_REPAIR_V3.md",
 "Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv",
 "Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A4_FIX4_INDEX_REPAIR_V3.sha256",
}

seen_logs=set()
bad=[]

prefix=relroot+"/"
for name in names:
    if name in allowed_exact:
        continue
    if name.startswith(prefix):
        inner=name[len(prefix):]
        if inner in expected_logs:
            seen_logs.add(inner)
        else:
            bad.append(name)
    else:
        bad.append(name)

if bad:
    print("UNEXPECTED_STAGED_FILES:")
    for x in bad:print(x)
    raise SystemExit(1)

if seen_logs!=expected_logs:
    print("MISSING_FROM_STAGE="+repr(sorted(expected_logs-seen_logs)))
    print("UNEXPECTED_LOG_STAGE="+repr(sorted(seen_logs-expected_logs)))
    raise SystemExit(1)

if len(seen_logs)!=26:
    raise SystemExit("STAGED_LOG_COUNT_NOT_26:"+str(len(seen_logs)))

print("STAGED_VALIDATION_LOGS=26")
print("STAGED_SCOPE_EXACT=true")
PY

echo
echo "============================================================"
echo " BELIAL A4 FIX4 INDEX/GIT REPAIR V3 = PASS"
echo "============================================================"
echo "PHYSICAL_SNAPSHOT_CANONICAL=true"
echo "HEAD_BEFORE_CANONICAL=false"
echo "STAGED_INDEX_A4_CANONICAL=true"
echo "MANIFEST_CHANGED=false"
echo "POOLS_CHANGED=false"
echo "ENGINE_CHANGED=false"
echo "TESTS_CHANGED=false"
echo "ORDERS=false"
echo
echo "COMMIT_REALIZADO=false"
echo "PUSH_REALIZADO=false"
echo
echo "Depois da revisao:"
echo
echo "cd \"$REPO\""
echo "git commit -m \"fix(a4): track canonical FIX4 validation members\""
echo "git push origin main"
echo
echo "Depois envie:"
echo "git status --short && git log -3 --oneline --decorate"
echo
echo "Nao rode o A5 novamente ate reancorarmos o bootstrap no novo commit."
