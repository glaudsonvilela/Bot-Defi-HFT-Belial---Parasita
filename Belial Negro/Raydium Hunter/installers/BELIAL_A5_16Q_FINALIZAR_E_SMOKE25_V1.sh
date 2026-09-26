#!/usr/bin/env bash
set -euo pipefail
umask 077

# BELIAL A5 16Q — FINALIZA CHECKPOINT + SMOKE 25 MIN V1
#
# Estado esperado:
#   - HEAD/origin-main ainda em bd554ed... (A4 FIX4 Git repair)
#   - BELIAL_A5_16Q_BOOTSTRAP_V9_COMPLETO chegou ao [9/9]
#   - snapshot A5 está staged, mas `git diff --cached --check` parou em 3
#     problemas de whitespace herdados byte-a-byte do A4.
#
# Este arquivo:
#   1) NÃO altera os 3 arquivos herdados com whitespace histórico;
#   2) prova que eles são byte-idênticos ao A4 canônico;
#   3) exige `git diff --check` limpo em TODO o restante do checkpoint;
#   4) valida o INDEX staged via git write-tree + git archive + a5.verify;
#   5) fecha commit/push do bootstrap A5;
#   6) executa um SMOKE de 25 minutos PAPER/SHADOW com watchdog;
#   7) coleta perfil leve de CPU/RSS, Rust→PAPER, UI e payloads;
#   8) interrompe propositalmente a sessão aos 25 min e exige replay íntegro.
#
# O smoke NÃO é uma sessão de 90m/9h certificada.
# Nenhuma ordem real.
#
# Depois deste baseline, a limpeza/performance será feita em linha isolada
# A5.1, usando estas métricas antes/depois.

EXPECTED_BASE="bd554ed580062bcfcd12bb0751ff516e3b8b0faa"

BASE="${BELIAL_BASE:-/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter}"
REPO="${BELIAL_GIT_REPO:-$HOME/Área de trabalho/belial-github-publicar}"

A4_REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a4-90m-v1-fix4"
A4="$REPO/$A4_REL"

A5_REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a5-16q-v1"
A5="$REPO/$A5_REL"
RELEASE="$BASE/releases/belial-a5-16q-v1"

BOOTSTRAP_INSTALLER_REL="Belial Negro/Raydium Hunter/installers/BELIAL_A5_16Q_BOOTSTRAP_V9_COMPLETO.sh"
BOOTSTRAP_HANDOFF_REL="docs/HANDOFF_A5_16Q_BOOTSTRAP_V9.md"
BOOTSTRAP_CHECKPOINT_REL="Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A5_16Q_BOOTSTRAP_V9.sha256"
DEEP_LOG_REL="Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv"

SELF="$(readlink -f "$0")"
FINALIZER_REL="Belial Negro/Raydium Hunter/installers/BELIAL_A5_16Q_FINALIZAR_E_SMOKE25_V1.sh"
FINALIZER_DEST="$REPO/$FINALIZER_REL"
FINALIZER_HANDOFF_REL="docs/HANDOFF_A5_BOOTSTRAP_FINALIZE_SMOKE25_PREP.md"
FINALIZER_HANDOFF="$REPO/$FINALIZER_HANDOFF_REL"
FINALIZER_CHECKPOINT_REL="Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A5_BOOTSTRAP_FINALIZE_SMOKE25_PREP.sha256"
FINALIZER_CHECKPOINT="$REPO/$FINALIZER_CHECKPOINT_REL"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
CONTROL="$BASE/evidence/a5-smoke25-control-$TS"
SESSION="$BASE/evidence/a5-smoke25-$TS"
PERF="$CONTROL/performance.jsonl"
SUP_LOG="$CONTROL/supervisor.log"
RESULT_ENV="$CONTROL/RESULT.env"

SMOKE_SECONDS=1500
PREP_TIMEOUT_SECONDS=1200
FINALIZE_TIMEOUT_SECONDS=220
SAMPLE_SECONDS=5

TMP="$(mktemp -d -t belial-a5-finalize-smoke25-XXXXXX)"

cleanup_tmp(){ rm -rf -- "$TMP"; }
trap cleanup_tmp EXIT

fail(){
  echo
  echo "BELIAL_A5_FINALIZE_SMOKE25_FAILED=$1" >&2
  exit 3
}

if [ "$(id -u)" -eq 0 ]; then
  fail "DO_NOT_RUN_AS_ROOT"
fi

for cmd in git python3 sha256sum cmp tar find diff cp mkdir awk grep sed date readlink kill ps; do
  command -v "$cmd" >/dev/null || fail "DEPENDENCY_MISSING:$cmd"
done

findmnt -rn --mountpoint /mnt/sherlock-ssd >/dev/null 2>&1 || fail "SSD_NOT_MOUNTED"
[ -d "$REPO/.git" ] || fail "GIT_REPO_NOT_FOUND"
[ -d "$A4" ] || fail "A4_CANONICAL_SNAPSHOT_NOT_FOUND"
[ -d "$A5" ] || fail "A5_STAGED_SNAPSHOT_NOT_FOUND"
[ -d "$RELEASE" ] || fail "A5_RELEASE_NOT_FOUND"

mkdir -p "$CONTROL"

echo "============================================================"
echo " BELIAL A5 16Q | FINALIZA CHECKPOINT + SMOKE 25 MIN"
echo "============================================================"
echo "REPO=$REPO"
echo "A5=$A5"
echo "RELEASE=$RELEASE"
echo "SESSION=$SESSION"
echo

# ---------------------------------------------------------------------------
# 1. Baseline Git
# ---------------------------------------------------------------------------
echo "[1/8] Baseline Git e estado staged"

cd "$REPO"
HEAD_NOW="$(git rev-parse HEAD)"
echo "HEAD=$HEAD_NOW"
[ "$HEAD_NOW" = "$EXPECTED_BASE" ] || fail "UNEXPECTED_HEAD:$HEAD_NOW"

if git fetch origin main >"$CONTROL/git-fetch.log" 2>&1; then
  REMOTE_NOW="$(git rev-parse origin/main)"
  echo "ORIGIN_MAIN=$REMOTE_NOW"
  [ "$REMOTE_NOW" = "$HEAD_NOW" ] || fail "LOCAL_REMOTE_DIVERGED"
else
  echo "GIT_FETCH_WARNING=true"
fi

# No unstaged tracked modifications are allowed.
UNSTAGED="$(git diff --name-only)"
if [ -n "$UNSTAGED" ]; then
  echo "Alteracoes tracked fora do INDEX:"
  printf '%s\n' "$UNSTAGED"
  fail "UNSTAGED_TRACKED_CHANGES"
fi

STAGED_COUNT="$(git diff --cached --name-only | wc -l | tr -d ' ')"
echo "STAGED_FILES_BEFORE=$STAGED_COUNT"
[ "$STAGED_COUNT" -gt 300 ] || fail "A5_STAGING_NOT_FOUND_OR_TOO_SMALL"

# Required V9 checkpoint artifacts must already be staged.
for rel in \
  "$BOOTSTRAP_INSTALLER_REL" \
  "$BOOTSTRAP_HANDOFF_REL" \
  "$BOOTSTRAP_CHECKPOINT_REL" \
  "$DEEP_LOG_REL" \
  "docs/CURRENT_STATE.md"; do
  git diff --cached --name-only -- "$rel" | grep -Fxq "$rel" \
    || fail "EXPECTED_STAGED_ARTIFACT_MISSING:$rel"
done

# ---------------------------------------------------------------------------
# 2. Package/release integrity
# ---------------------------------------------------------------------------
echo "[2/8] Integridade A5 local e release imutavel"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export BELIAL_A1_NO_BROWSER=1

(
  cd "$A5"
  PYTHONPATH="$A5" python3 -B -m a5.verify --hashes-only
) | tee "$CONTROL/a5-snapshot-hashes.log"

grep -Fq "A5_PACKAGE_HASHES=PASS" "$CONTROL/a5-snapshot-hashes.log" \
  || fail "A5_SNAPSHOT_HASH_GATE"

(
  cd "$RELEASE"
  PYTHONPATH="$RELEASE" python3 -B -m a5.verify --hashes-only
) | tee "$CONTROL/a5-release-hashes.log"

grep -Fq "A5_PACKAGE_HASHES=PASS" "$CONTROL/a5-release-hashes.log" \
  || fail "A5_RELEASE_HASH_GATE"

if ! diff -qr -- "$A5" "$RELEASE" >"$CONTROL/a5-release-vs-snapshot.diff" 2>&1; then
  cat "$CONTROL/a5-release-vs-snapshot.diff" >&2
  fail "A5_RELEASE_SNAPSHOT_DIFFER"
fi
echo "A5_RELEASE_EQUALS_SNAPSHOT=true"

# ---------------------------------------------------------------------------
# 3. Whitespace gate: preserve inherited bytes, reject every NEW issue
# ---------------------------------------------------------------------------
echo "[3/8] Whitespace gate com preservacao byte-a-byte"

WS_ALLOW=(
  "legacy/runner/tools/preflight_pools.py"
  "tests_a3/test_processes.py"
  "tests_a4/test_processes.py"
)

for rel in "${WS_ALLOW[@]}"; do
  [ -f "$A4/$rel" ] || fail "A4_WS_SOURCE_MISSING:$rel"
  [ -f "$A5/$rel" ] || fail "A5_WS_SOURCE_MISSING:$rel"
  cmp -s -- "$A4/$rel" "$A5/$rel" || fail "INHERITED_WS_FILE_CHANGED:$rel"
  A4_SHA="$(sha256sum "$A4/$rel" | awk '{print $1}')"
  A5_SHA="$(sha256sum "$A5/$rel" | awk '{print $1}')"
  [ "$A4_SHA" = "$A5_SHA" ] || fail "INHERITED_WS_HASH_CHANGED:$rel"
  echo "PRESERVED_WS_EXCEPTION=$rel sha256=$A5_SHA"
done

# Capture the raw warnings for evidence.
set +e
git diff --cached --check >"$CONTROL/diff-check-raw.log" 2>&1
RAW_CHECK_RC=$?
set -e
echo "RAW_DIFF_CHECK_RC=$RAW_CHECK_RC"

# The entire checkpoint except the exact three inherited files must be clean.
EXCLUDE1=":(exclude)$A5_REL/${WS_ALLOW[0]}"
EXCLUDE2=":(exclude)$A5_REL/${WS_ALLOW[1]}"
EXCLUDE3=":(exclude)$A5_REL/${WS_ALLOW[2]}"

git diff --cached --check -- . "$EXCLUDE1" "$EXCLUDE2" "$EXCLUDE3" \
  >"$CONTROL/diff-check-scoped.log" 2>&1 || {
    cat "$CONTROL/diff-check-scoped.log" >&2
    fail "NEW_WHITESPACE_ERROR_OUTSIDE_PRESERVED_FILES"
  }

# Prove raw diff-check headers only refer to the known preserved paths.
python3 - "$CONTROL/diff-check-raw.log" "$A5_REL" <<'PY'
from pathlib import Path
import sys,re

log=Path(sys.argv[1]).read_text(errors='replace').splitlines()
root=sys.argv[2]
allowed={
    f"{root}/legacy/runner/tools/preflight_pools.py",
    f"{root}/tests_a3/test_processes.py",
    f"{root}/tests_a4/test_processes.py",
}
headers=[]
for line in log:
    m=re.match(r'^(.*?):(\d+): (trailing whitespace\.|new blank line at EOF\.)$',line)
    if m:
        headers.append((m.group(1),m.group(3)))

bad=[x for x in headers if x[0] not in allowed]
if bad:
    raise SystemExit("UNEXPECTED_WHITESPACE_HEADERS:"+repr(bad))
if set(p for p,_ in headers)!=allowed:
    raise SystemExit("EXPECTED_THREE_PRESERVED_WHITESPACE_FILES:"+repr(headers))
print("PRESERVED_WHITESPACE_ONLY=true")
PY

echo "NEW_WHITESPACE_OUTSIDE_PRESERVED=false"

# ---------------------------------------------------------------------------
# 4. Add finalizer provenance, verify staged INDEX exactly
# ---------------------------------------------------------------------------
echo "[4/8] Proveniencia final + prova do INDEX staged"

mkdir -p "$(dirname "$FINALIZER_DEST")" "$(dirname "$FINALIZER_HANDOFF")"
cp -- "$SELF" "$FINALIZER_DEST"
chmod 0755 "$FINALIZER_DEST"

cat > "$FINALIZER_HANDOFF" <<EOF
# Bot Belial — A5 bootstrap finalization / Smoke25 preparation

UTC: $TS

## Estado

O bootstrap A5 16Q chegou ao staging após todos os gates de pacote, Python,
A4 regression e Rust.

O \`git diff --cached --check\` apontou apenas três defeitos de whitespace
pré-existentes em arquivos herdados do A4:

- \`legacy/runner/tools/preflight_pools.py\`
- \`tests_a3/test_processes.py\`
- \`tests_a4/test_processes.py\`

Eles foram preservados byte-a-byte para não quebrar proveniência/hashes.
Nenhum novo erro de whitespace fora desses três arquivos é aceito.

## Próximo gate

Após commit/push do bootstrap, este mesmo instalador executa um smoke PAPER /
SHADOW intencionalmente interrompido aos 25 minutos.

O smoke é baseline de software/performance. Não é uma sessão integral de 90m
nem a futura sessão de 9h.
EOF

if [ ! -f "$REPO/$DEEP_LOG_REL" ]; then
  fail "DEEP_CHANGE_LOG_MISSING"
fi

if ! grep -Fq "A5_16Q_FINALIZE_SMOKE25_PREP" "$REPO/$DEEP_LOG_REL"; then
  printf '%s\tA5_16Q_FINALIZE_SMOKE25_PREP\tSTAGED_INDEX_FINALIZATION\tbelial-a5-16q-v1\t%s\t%s\n' \
    "$TS" \
    "$(sha256sum "$A5/MANIFEST_SHA256.json" | awk '{print $1}')" \
    "$(sha256sum "$FINALIZER_DEST" | awk '{print $1}')" \
    >> "$REPO/$DEEP_LOG_REL"
fi

cat > "$FINALIZER_CHECKPOINT" <<EOF
$(sha256sum "$A5/MANIFEST_SHA256.json")
$(sha256sum "$FINALIZER_DEST")
$(sha256sum "$FINALIZER_HANDOFF")
EOF

git add -- \
  "$FINALIZER_REL" \
  "$FINALIZER_HANDOFF_REL" \
  "$FINALIZER_CHECKPOINT_REL" \
  "$DEEP_LOG_REL"

# Again: zero new whitespace errors outside exact inherited allowlist.
git diff --cached --check -- . "$EXCLUDE1" "$EXCLUDE2" "$EXCLUDE3" \
  >"$CONTROL/diff-check-final.log" 2>&1 || {
    cat "$CONTROL/diff-check-final.log" >&2
    fail "FINAL_NEW_WHITESPACE_ERROR"
  }

TREE_SHA="$(git write-tree)"
echo "A5_STAGED_TREE_SHA=$TREE_SHA"

INDEX_EXPORT="$TMP/index-export"
mkdir -p "$INDEX_EXPORT"
git archive --format=tar "$TREE_SHA" -- "$A5_REL" | tar -xf - -C "$INDEX_EXPORT"
INDEX_A5="$INDEX_EXPORT/$A5_REL"

(
  cd "$INDEX_A5"
  PYTHONPATH="$INDEX_A5" python3 -B -m a5.verify --hashes-only
) | tee "$CONTROL/a5-index-hashes.log"

grep -Fq "A5_PACKAGE_HASHES=PASS" "$CONTROL/a5-index-hashes.log" \
  || fail "A5_STAGED_INDEX_HASH_GATE"

# Exact staged scope: snapshot + known bootstrap/finalizer metadata only.
python3 - "$REPO" "$A5_REL" <<'PY'
from pathlib import Path
import subprocess,sys
repo=Path(sys.argv[1]);prefix=sys.argv[2]+'/'
names=subprocess.check_output(
    ['git','-C',str(repo),'diff','--cached','--name-only'],
    text=True
).splitlines()
allowed={
 'Belial Negro/Raydium Hunter/installers/BELIAL_A5_16Q_BOOTSTRAP_V9_COMPLETO.sh',
 'Belial Negro/Raydium Hunter/installers/BELIAL_A5_16Q_FINALIZAR_E_SMOKE25_V1.sh',
 'Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv',
 'Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A5_16Q_BOOTSTRAP_V9.sha256',
 'Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A5_BOOTSTRAP_FINALIZE_SMOKE25_PREP.sha256',
 'docs/HANDOFF_A5_16Q_BOOTSTRAP_V9.md',
 'docs/HANDOFF_A5_BOOTSTRAP_FINALIZE_SMOKE25_PREP.md',
 'docs/CURRENT_STATE.md',
}
bad=[]
snap=0
for name in names:
    if name.startswith(prefix):
        snap+=1
    elif name not in allowed:
        bad.append(name)
if bad:
    raise SystemExit("UNEXPECTED_STAGED_FILES:"+repr(bad))
if snap < 360:
    raise SystemExit("A5_STAGED_SNAPSHOT_TOO_SMALL:"+str(snap))
print("A5_STAGED_SNAPSHOT_FILES="+str(snap))
print("A5_STAGED_SCOPE_EXACT=true")
PY

echo "STAGED_INDEX_A5_CANONICAL=true"

# ---------------------------------------------------------------------------
# 5. Commit + push bootstrap
# ---------------------------------------------------------------------------
echo "[5/8] Commit + push do checkpoint A5"

git commit -m "feat(a5): bootstrap isolated 16Q research line"
A5_COMMIT="$(git rev-parse HEAD)"
echo "A5_COMMIT_SHA=$A5_COMMIT"

git push origin main
git fetch origin main >/dev/null 2>&1 || true
REMOTE_AFTER="$(git rev-parse origin/main)"
[ "$REMOTE_AFTER" = "$A5_COMMIT" ] || fail "REMOTE_HEAD_NOT_EQUAL_A5_COMMIT"

if [ -n "$(git status --porcelain=v1)" ]; then
  git status --short
  fail "WORKTREE_NOT_CLEAN_AFTER_A5_COMMIT"
fi
echo "A5_BOOTSTRAP_GIT=PASS"

# ---------------------------------------------------------------------------
# 6. Smoke preflight and launch
# ---------------------------------------------------------------------------
echo "[6/8] Preparando Smoke25 PAPER/SHADOW"

[ ! -e "$SESSION" ] || fail "SMOKE_SESSION_ALREADY_EXISTS"

(
  cd "$RELEASE"
  PYTHONPATH="$RELEASE" python3 -B -m a5.verify --hashes-only
) >"$CONTROL/pre-smoke-release-verify.log" 2>&1 \
  || { cat "$CONTROL/pre-smoke-release-verify.log" >&2; fail "PRE_SMOKE_RELEASE_VERIFY"; }

echo "SMOKE25_SECONDS=$SMOKE_SECONDS"
echo "SMOKE25_IS_FULL_EXPERIMENT=false"
echo "ORDERS=false"

SMOKE_ACTIVE=0
SUP_PID=""

stop_smoke(){
  if [ "${SMOKE_ACTIVE:-0}" -eq 1 ] && [ -n "${SUP_PID:-}" ] && kill -0 "$SUP_PID" 2>/dev/null; then
    kill -INT "$SUP_PID" 2>/dev/null || true
    sleep 2
    kill -TERM "$SUP_PID" 2>/dev/null || true
  fi
}
trap 'stop_smoke; cleanup_tmp' EXIT INT TERM

(
  cd "$RELEASE"
  exec env \
    PYTHONPATH="$RELEASE" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BELIAL_A1_NO_BROWSER=1 \
    python3 -B -m a5.supervisor --session "$SESSION"
) >"$SUP_LOG" 2>&1 &

SUP_PID=$!
SMOKE_ACTIVE=1
echo "A5_SMOKE_SUPERVISOR_PID=$SUP_PID"

# Wait until CAPTURING. Preparation time is not counted in the 25 minutes.
PREP_STARTED="$(date +%s)"
LAST_PREP_PRINT=0

while :; do
  if ! kill -0 "$SUP_PID" 2>/dev/null; then
    wait "$SUP_PID" || true
    tail -n 240 "$SUP_LOG" >&2 || true
    fail "SUPERVISOR_EXITED_BEFORE_CAPTURE"
  fi

  PHASE="$(python3 - "$SESSION" <<'PY'
from pathlib import Path
import json,sys
p=Path(sys.argv[1])/'SESSION.json'
try: print(json.loads(p.read_text()).get('phase',''))
except Exception: print('')
PY
)"

  if [ "$PHASE" = "CAPTURING" ]; then
    break
  fi

  NOW="$(date +%s)"
  if [ $((NOW-PREP_STARTED)) -ge "$PREP_TIMEOUT_SECONDS" ]; then
    kill -INT "$SUP_PID" 2>/dev/null || true
    tail -n 260 "$SUP_LOG" >&2 || true
    fail "PREPARATION_TIMEOUT"
  fi

  if [ $((NOW-LAST_PREP_PRINT)) -ge 30 ]; then
    echo "SMOKE25_PREPARING elapsed=$((NOW-PREP_STARTED))s phase=${PHASE:-UNKNOWN}"
    LAST_PREP_PRINT=$NOW
  fi
  sleep 2
done

CAPTURE_STARTED_EPOCH="$(date +%s)"
echo "A5_SMOKE25_CAPTURE_STARTED=true"
echo "A5_SMOKE25_CAPTURE_EPOCH=$CAPTURE_STARTED_EPOCH"

# ---------------------------------------------------------------------------
# 7. 25-minute watcher + lightweight profiler
# ---------------------------------------------------------------------------
echo "[7/8] Smoke25 em andamento + perfil leve"

sample_perf(){
python3 - "$SUP_PID" "$SESSION" "$PERF" <<'PY'
from pathlib import Path
import json,os,sys,time,urllib.request

root_pid=int(sys.argv[1]);session=Path(sys.argv[2]);out=Path(sys.argv[3])

def jread(path,default=None):
    try:return json.loads(Path(path).read_text())
    except Exception:return default

# Process tree from /proc.
rows={}
for p in Path('/proc').glob('[0-9]*'):
    try:
        status={}
        for line in (p/'status').read_text(errors='replace').splitlines():
            if ':' in line:
                k,v=line.split(':',1);status[k]=v.strip()
        pid=int(p.name);ppid=int(status.get('PPid','0'))
        rss=int(status.get('VmRSS','0 kB').split()[0])
        raw=(p/'stat').read_text()
        end=raw.rfind(')')
        fields=raw[end+2:].split()
        hz=os.sysconf(os.sysconf_names['SC_CLK_TCK'])
        cpu=(int(fields[11])+int(fields[12]))/hz
        cmd=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace').strip()
        rows[pid]=dict(pid=pid,ppid=ppid,rss_kb=rss,cpu_seconds=cpu,cmd=cmd)
    except Exception:
        pass

desc={root_pid}
changed=True
while changed:
    changed=False
    for pid,row in rows.items():
        if pid not in desc and row['ppid'] in desc:
            desc.add(pid);changed=True

procs=[rows[p] for p in sorted(desc) if p in rows]
total_rss=sum(p['rss_kb'] for p in procs)
total_cpu=sum(p['cpu_seconds'] for p in procs)

books=jread(session/'paper/books.json',{}) or {}
ui=jread(session/'ui/status.json',{}) or {}
platform=jread(session/'PLATFORM.json',{}) or {}
paper=jread(session/'paper/status.json',{}) or {}
n2=jread(session/'n2/status.json',{}) or {}
integrity=jread(session/'INTEGRITY.json',{'ok':True}) or {'ok':True}

http_ms=None;http_ok=False
url=(jread(session/'ui/URL.json',{}) or {}).get('url')
if url:
    started=time.perf_counter()
    try:
        with urllib.request.urlopen(url+'health',timeout=.8) as r:
            r.read()
            http_ok=(r.status==200)
    except Exception:
        http_ok=False
    http_ms=(time.perf_counter()-started)*1000

row={
 'at_ms':time.time_ns()//1_000_000,
 'process_count':len(procs),
 'rss_kb':total_rss,
 'cpu_seconds':total_cpu,
 'messages_per_second':books.get('messages_per_second'),
 'rust_to_paper_ms_p50':books.get('rust_to_paper_ms_p50'),
 'rust_to_paper_ms_p95':books.get('rust_to_paper_ms_p95'),
 'ingest_queue':books.get('ingest_queue'),
 'ui_updates_per_second':ui.get('display_updates_per_second'),
 'ui_max_payload_bytes':ui.get('maximum_payload_bytes'),
 'ui_http_ms':http_ms,
 'ui_http_ok':http_ok,
 'paper_state':paper.get('state'),
 'n2_state':n2.get('state'),
 'integrity_ok':integrity.get('ok',True),
 'coverage':(platform.get('coverage') or {}),
}
with out.open('a',encoding='utf-8') as f:
    f.write(json.dumps(row,separators=(',',':'),sort_keys=True)+'\n')

if integrity.get('ok') is False:
    raise SystemExit(11)
PY
}

LAST_PROGRESS=0
EARLY_INTEGRITY_FAIL=0

while :; do
  NOW="$(date +%s)"
  ELAPSED=$((NOW-CAPTURE_STARTED_EPOCH))
  if [ "$ELAPSED" -ge "$SMOKE_SECONDS" ]; then
    break
  fi

  if ! kill -0 "$SUP_PID" 2>/dev/null; then
    wait "$SUP_PID" || true
    tail -n 280 "$SUP_LOG" >&2 || true
    fail "SUPERVISOR_EXITED_DURING_SMOKE"
  fi

  set +e
  sample_perf
  SAMPLE_RC=$?
  set -e
  if [ "$SAMPLE_RC" -eq 11 ]; then
    EARLY_INTEGRITY_FAIL=1
    echo "SMOKE25_INTEGRITY_FAILED_EARLY=true"
    break
  elif [ "$SAMPLE_RC" -ne 0 ]; then
    fail "PERFORMANCE_SAMPLER_FAILED:$SAMPLE_RC"
  fi

  if [ $((NOW-LAST_PROGRESS)) -ge 60 ]; then
    echo "SMOKE25_PROGRESS elapsed=${ELAPSED}s / ${SMOKE_SECONDS}s"
    LAST_PROGRESS=$NOW
  fi

  sleep "$SAMPLE_SECONDS"
done

echo "SMOKE25_REQUESTING_GRACEFUL_STOP=true"
kill -INT "$SUP_PID" 2>/dev/null || true

FINALIZE_STARTED="$(date +%s)"
while kill -0 "$SUP_PID" 2>/dev/null; do
  NOW="$(date +%s)"
  if [ $((NOW-FINALIZE_STARTED)) -ge "$FINALIZE_TIMEOUT_SECONDS" ]; then
    kill -TERM "$SUP_PID" 2>/dev/null || true
    sleep 3
    kill -KILL "$SUP_PID" 2>/dev/null || true
    fail "SMOKE_FINALIZATION_TIMEOUT"
  fi
  sleep 2
done

set +e
wait "$SUP_PID"
SUP_RC=$?
set -e
SMOKE_ACTIVE=0
echo "A5_SMOKE_SUPERVISOR_RC=$SUP_RC"

# ---------------------------------------------------------------------------
# 8. Final software/performance report
# ---------------------------------------------------------------------------
echo "[8/8] Avaliando Smoke25"

python3 - "$SESSION" "$PERF" "$CONTROL/SMOKE25_REPORT.json" "$SUP_LOG" "$EARLY_INTEGRITY_FAIL" <<'PY'
from pathlib import Path
import json,statistics,sys

session=Path(sys.argv[1]);perf=Path(sys.argv[2]);out=Path(sys.argv[3]);log=Path(sys.argv[4])
early_integrity=int(sys.argv[5])

def read(path,default=None):
    try:return json.loads(Path(path).read_text())
    except Exception:return default

summary=read(session/'SUMMARY.json',{}) or {}
replay=read(session/'REPLAY_CHECK.json',{}) or {}
paper=read(session/'paper/COMPLETE.json',{}) or {}
integrity=read(session/'INTEGRITY.json',{}) or {}
platform=read(session/'PLATFORM.json',{}) or {}
state=read(session/'SESSION.json',{}) or {}

samples=[]
if perf.exists():
    for line in perf.read_text().splitlines():
        if line.strip():
            try:samples.append(json.loads(line))
            except Exception:pass

def vals(key):
    out=[]
    for r in samples:
        v=r.get(key)
        if isinstance(v,(int,float)) and not isinstance(v,bool):
            out.append(float(v))
    return out

def pct(xs,p):
    if not xs:return None
    ys=sorted(xs)
    i=min(len(ys)-1,max(0,int(round((len(ys)-1)*p))))
    return ys[i]

rss=vals('rss_kb')
cpu=vals('cpu_seconds')
mps=vals('messages_per_second')
lat95=vals('rust_to_paper_ms_p95')
http=vals('ui_http_ms')
payload=vals('ui_max_payload_bytes')
queue=vals('ingest_queue')

critical_keys={'supervisor','paper','analysis','n2','disk'}
failures=summary.get('failures') or {}
critical_failures={k:v for k,v in failures.items() if k in critical_keys}

software_ok=all([
    summary.get('capture_started') is True,
    summary.get('orders') is False,
    summary.get('interrupted') is True,
    integrity.get('ok') is True,
    early_integrity==0,
    replay.get('output_matches_live') is True,
    paper.get('ok') is True,
    not critical_failures,
    len(samples)>=100,
])

coverage=platform.get('coverage') or {}
report={
 'schema':'belial_a5_smoke25_report_v1',
 'software_gate_pass':software_ok,
 'intentional_interrupt_after_seconds':1500,
 'full_experiment':False,
 'orders':False,
 'paper':True,
 'shadow':True,
 'capture_started':summary.get('capture_started'),
 'summary_interrupted':summary.get('interrupted'),
 'integrity':integrity,
 'replay':replay,
 'paper_complete':paper,
 'critical_failures':critical_failures,
 'all_failures':failures,
 'coverage_final':{
   'requested':coverage.get('requested'),
   'qualified_at_start':coverage.get('qualified_at_start'),
   'live_signal_sources':coverage.get('live_signal_sources'),
   'usable_books':coverage.get('usable_books'),
   'paper_prerequisites_ready':coverage.get('paper_prerequisites_ready'),
 },
 'performance':{
   'samples':len(samples),
   'rss_peak_mb':max(rss)/1024 if rss else None,
   'rss_median_mb':statistics.median(rss)/1024 if rss else None,
   'cpu_seconds_delta':(cpu[-1]-cpu[0]) if len(cpu)>=2 else None,
   'messages_per_second_median':statistics.median(mps) if mps else None,
   'messages_per_second_p95':pct(mps,.95),
   'rust_to_paper_ms_p95_median':statistics.median(lat95) if lat95 else None,
   'rust_to_paper_ms_p95_peak':max(lat95) if lat95 else None,
   'ui_http_ms_p50':statistics.median(http) if http else None,
   'ui_http_ms_p95':pct(http,.95),
   'ui_max_payload_bytes_peak':max(payload) if payload else None,
   'ingest_queue_peak':max(queue) if queue else None,
   'ui_health_success_ratio':(
      sum(1 for r in samples if r.get('ui_http_ok') is True)/len(samples)
      if samples else None
   ),
 },
 'session':str(session),
}
out.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')

print("SMOKE25_SOFTWARE_GATE_PASS="+str(software_ok).lower())
print("SMOKE25_SAMPLES="+str(len(samples)))
print("SMOKE25_INTEGRITY_OK="+str(integrity.get('ok') is True).lower())
print("SMOKE25_REPLAY_MATCH="+str(replay.get('output_matches_live') is True).lower())
print("SMOKE25_PAPER_COMPLETE="+str(paper.get('ok') is True).lower())
print("SMOKE25_CRITICAL_FAILURES="+repr(critical_failures))
print("SMOKE25_COVERAGE="+repr(report['coverage_final']))
print("SMOKE25_PERFORMANCE="+repr(report['performance']))
print("SMOKE25_REPORT="+str(out))

if not software_ok:
    tail=log.read_text(errors='replace')[-16000:] if log.exists() else ''
    print("SMOKE25_LOG_TAIL_BEGIN")
    print(tail)
    print("SMOKE25_LOG_TAIL_END")
    raise SystemExit(2)
PY

cat > "$RESULT_ENV" <<EOF
BELIAL_A5_BOOTSTRAP_FINALIZED=true
A5_COMMIT_SHA=$A5_COMMIT
ORIGIN_MAIN=$REMOTE_AFTER
SMOKE25_SECONDS=$SMOKE_SECONDS
SMOKE25_SESSION=$SESSION
SMOKE25_REPORT=$CONTROL/SMOKE25_REPORT.json
SMOKE25_FULL_EXPERIMENT=false
SMOKE25_ORDERS=false
EOF

echo
echo "============================================================"
echo " BELIAL A5 16Q | CHECKPOINT + SMOKE25 = PASS"
echo "============================================================"
cat "$RESULT_ENV"
echo
echo "O proximo passo NAO sera limpeza cega."
echo "Primeiro compare as metricas deste baseline; depois A5.1 cleanup/performance;"
echo "entao repetimos 25 min e somente depois abrimos a sessao de 9 horas."
