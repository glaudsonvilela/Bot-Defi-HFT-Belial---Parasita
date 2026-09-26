#!/usr/bin/env bash
set -euo pipefail
umask 077

# BELIAL A5 16Q - BOOTSTRAP V9 COMPLETO
# Linha isolada derivada do A4 FIX4 canônico após o reparo do INDEX/Git.
# PAPER / SHADOW somente. Nenhuma ordem real.
# Este arquivo NAO inicia a captura de 90 minutos.
# Invariante: o snapshot A5 staged, exportado via git write-tree/git archive,
# deve passar a5.verify antes de qualquer commit. Isso impede perda silenciosa
# de membros canonicos ignorados, inclusive validation/*.log.
# V3 COMPLETO: depois de TODOS os gates verdes, cria commit e push automaticamente.
# Nao inicia a captura de 90 minutos.

EXPECTED_HEAD="bd554ed580062bcfcd12bb0751ff516e3b8b0faa"

BASE="${BELIAL_BASE:-/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter}"
REPO="${BELIAL_GIT_REPO:-$HOME/Área de trabalho/belial-github-publicar}"

SOURCE_REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a4-90m-v1-fix4"
SOURCE="$REPO/$SOURCE_REL"

NEW_REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a5-16q-v1"
NEW_SNAPSHOT="$REPO/$NEW_REL"

INSTALLER_REL="Belial Negro/Raydium Hunter/installers/BELIAL_A5_16Q_BOOTSTRAP_V9_COMPLETO.sh"
INSTALLER_DEST="$REPO/$INSTALLER_REL"

RELEASE_NAME="belial-a5-16q-v1"
DEST="$BASE/releases/$RELEASE_NAME"
TARGET="$BASE/target-belial-a5-16q"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE="$BASE/evidence/a5-16q-bootstrap/$TS"
BACKUP="$BASE/.belial-backups/a5-16q-bootstrap-$TS"

SELF="$(readlink -f "$0")"
TMP="$(mktemp -d -t belial-a5-16q-XXXXXX)"
EXPORT="$TMP/git-export"
SOURCE_CANONICAL="$EXPORT/$SOURCE_REL"
WORK="$TMP/BELIAL_A5_16Q_V1"

cleanup() { rm -rf -- "$TMP"; }
trap cleanup EXIT

fail() {
  echo
  echo "BELIAL_A5_16Q_BOOTSTRAP_FAILED=$1" >&2
  exit 3
}

if [ "$(id -u)" -eq 0 ]; then
  fail "DO_NOT_RUN_AS_ROOT"
fi

for cmd in git python3 sha256sum find diff cp mkdir awk sed grep cargo findmnt tee tail mv date readlink chmod tar wc; do
  command -v "$cmd" >/dev/null || fail "DEPENDENCY_MISSING:$cmd"
done

echo "============================================================"
echo " BELIAL A5 16Q | BOOTSTRAP V9 COMPLETO | PAPER / SHADOW"
echo "============================================================"
echo "BASE=$BASE"
echo "REPO=$REPO"
echo "SOURCE=$SOURCE"
echo "DEST=$DEST"
echo

findmnt -rn --mountpoint /mnt/sherlock-ssd >/dev/null || fail "SSD_NOT_MOUNTED"
[ -d "$BASE" ] || fail "BELIAL_BASE_NOT_FOUND"
[ -d "$REPO/.git" ] || fail "GIT_REPO_NOT_FOUND"
[ -d "$SOURCE" ] || fail "A4_FIX4_SOURCE_NOT_FOUND"

mkdir -p "$EVIDENCE" "$BACKUP" "$BASE/releases"

echo "[0/9] Baseline Git"

(
  cd "$REPO"
  git rev-parse HEAD > "$BACKUP/git-head-before.txt"
  git status --porcelain=v1 > "$BACKUP/git-status-before.txt"
  git log -6 --oneline --decorate > "$BACKUP/git-log-before.txt"
)

if [ -s "$BACKUP/git-status-before.txt" ]; then
  echo "Working tree possui alteracoes:"
  cat "$BACKUP/git-status-before.txt"
  fail "GIT_WORKTREE_NOT_CLEAN"
fi

HEAD_NOW="$(git -C "$REPO" rev-parse HEAD)"
[ "$HEAD_NOW" = "$EXPECTED_HEAD" ] || fail "UNEXPECTED_BASELINE_HEAD:$HEAD_NOW"

if git -C "$REPO" fetch origin main >"$EVIDENCE/git-fetch.log" 2>&1; then
  REMOTE_HEAD="$(git -C "$REPO" rev-parse origin/main)"
  [ "$REMOTE_HEAD" = "$HEAD_NOW" ] || fail "LOCAL_REMOTE_DIVERGED"
else
  echo "GIT_FETCH_WARNING=true"
fi

sha256sum \
  "$SOURCE/MANIFEST_SHA256.json" \
  "$SOURCE/UNIVERSE_A2.json" \
  "$SOURCE/a4/common.py" \
  "$SOURCE/a4/supervisor.py" \
  "$SOURCE/engine_a4/src/main.rs" \
  "$SOURCE/engine_a4/src/bin/belial-spot-raw.rs" \
  > "$BACKUP/a4-critical-before.sha256"

echo "[1/9] Export canonico A4 FIX4"

mkdir -p "$EXPORT"
(
  cd "$REPO"
  git archive --format=tar HEAD -- "$SOURCE_REL"
) | tar -xf - -C "$EXPORT"

[ -d "$SOURCE_CANONICAL" ] || fail "CANONICAL_A4_EXPORT_NOT_FOUND"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export BELIAL_A1_NO_BROWSER=1

(
  cd "$SOURCE_CANONICAL"
  PYTHONPATH="$SOURCE_CANONICAL" python3 -B -m a4.verify --hashes-only
) | tee "$EVIDENCE/a4-source-hashes.log"

grep -Fq "A4_PACKAGE_HASHES=PASS" "$EVIDENCE/a4-source-hashes.log" \
  || fail "A4_SOURCE_HASH_GATE_FAILED"

echo "[2/9] Derivando namespace A5 + engine_a5 + tests_a5"

mkdir -p "$WORK"
cp -a "$SOURCE_CANONICAL/." "$WORK/"
cp -- "$WORK/MANIFEST_SHA256.json" "$WORK/BASELINE_A4_FIX4_MANIFEST_SHA256.json"

cp -a "$WORK/a4" "$WORK/a5"
cp -a "$WORK/engine_a4" "$WORK/engine_a5"
cp -a "$WORK/tests_a4" "$WORK/tests_a5"

if [ -f "$WORK/tests_a5/paper_harness_a4.py" ]; then
  mv "$WORK/tests_a5/paper_harness_a4.py" "$WORK/tests_a5/paper_harness_a5.py"
fi

python3 - "$WORK" "$EXPECTED_HEAD" <<'PY'
from pathlib import Path
import hashlib
import json
import re
import sys

root = Path(sys.argv[1])
base_commit = sys.argv[2]

CORE12 = (
    "FARTCOIN","BOME","WIF","POPCAT","RAY","JUP",
    "BONK","PENGU","PUMP","USELESS","GRASS","PYTH",
)
FOCUS4 = ("RENDER","KMNO","MEW","MPLX")
SYMBOLS = CORE12 + FOCUS4

FOCUS_ROWS = [
    {"symbol":"RENDER","mint":"rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof","decimals":8,
     "source":"https://raw.githubusercontent.com/drift-labs/protocol-v2/master/sdk/src/constants/spotMarkets.ts","cohort":"FOCUS4"},
    {"symbol":"KMNO","mint":"KMNo3nJsBXfcpJTVhZcXLW7RmTwTt4GVFE7suUBo9sS","decimals":6,
     "source":"https://kamino.com/docs/build/portfolio/get-portfolio-source-data-for-a-wallet","cohort":"FOCUS4"},
    {"symbol":"MEW","mint":"MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5","decimals":5,
     "source":"https://raw.githubusercontent.com/jup-ag/token-list/main/validated-tokens.csv","cohort":"FOCUS4"},
    {"symbol":"MPLX","mint":"METAewgxyPbgwsseH8T16a39CQ5VyVxZi9zXiDPY18m","decimals":6,
     "source":"https://raw.githubusercontent.com/jup-ag/token-list/main/validated-tokens.csv","cohort":"FOCUS4"},
]

universe = {
    "schema":"belial_a5_16q_universe_v1",
    "date_checked":"2026-09-26",
    "core12":list(CORE12),
    "focus4":FOCUS_ROWS,
    "selection":"Pinned 16Q research universe derived from A4 FIX4; quality/freshness focus; no runtime substitution; not investment advice.",
    "requested_assets":16,
    "quote":"WSOL",
    "orders":False,
}
(root/"UNIVERSE_A5_16Q.json").write_text(
    json.dumps(universe,indent=2,ensure_ascii=False)+"\n",encoding="utf-8"
)

# Namespace copy: module references and old expansion group become A5/FOCUS4.
for p in sorted((root/"a5").rglob("*")):
    if not p.is_file() or p.suffix not in {".py",".html"}:
        continue
    s=p.read_text(encoding="utf-8")
    s=s.replace("a4.","a5.")
    s=s.replace("EXPANSION12","FOCUS4")
    s=s.replace("shadow_expansion","shadow_focus")
    if p.suffix==".py":
        s=s.replace("BELIAL_A4_","BELIAL_A5_")
        s=s.replace("A4_AGENT_FAILED","A5_AGENT_FAILED")
    p.write_text(s,encoding="utf-8")

# common.py
p=root/"a5/common.py"
s=p.read_text(encoding="utf-8")
s,n=re.subn(r"FOCUS4=\([^\n]*\)","FOCUS4=('RENDER', 'KMNO', 'MEW', 'MPLX')",s,count=1)
if n!=1:raise SystemExit("A5_COMMON_FOCUS4_PATCH_FAILED")
s=s.replace("VERSION='BELIAL_A5_90M_V1_FIX4'","VERSION='BELIAL_A5_16Q_V1'")
if "SYMBOLS=CORE12+FOCUS4" not in s:raise SystemExit("A5_COMMON_SYMBOLS_CONTRACT_MISSING")
p.write_text(s,encoding="utf-8")

# catalog.py
p=root/"a5/catalog.py"
s=p.read_text(encoding="utf-8")
s=s.replace("integral_mp24_at_start","integral_16q_at_start")
s=s.replace("len(approved)==24","len(approved)==16")
p.write_text(s,encoding="utf-8")

# spot_prepare.py
p=root/"a5/spot_prepare.py"
s=p.read_text(encoding="utf-8")
s=s.replace("ROOT/'UNIVERSE_A2.json'","ROOT/'UNIVERSE_A5_16Q.json'")
s=s.replace("registry['expansion12']","registry['focus4']")
p.write_text(s,encoding="utf-8")

# build_worker.py
p=root/"a5/build_worker.py"
s=p.read_text(encoding="utf-8").replace("engine_a4/Cargo.toml","engine_a5/Cargo.toml")
p.write_text(s,encoding="utf-8")

# install.py
p=root/"a5/install.py"
s=p.read_text(encoding="utf-8")
s=s.replace("RELEASE='belial-a4-90m-v1-fix4'","RELEASE='belial-a5-16q-v1'")
s=s.replace(".belial-a4-install.lock",".belial-a5-install.lock")
s=s.replace("A4 1/3","A5 1/3").replace("A4 2/3","A5 2/3").replace("A4 3/3","A5 3/3")
s=s.replace("Release A4 local","Release A5 local")
s=s.replace("BELIAL_A4_INSTALL_FAILED","BELIAL_A5_INSTALL_FAILED")
p.write_text(s,encoding="utf-8")

# supervisor.py
p=root/"a5/supervisor.py"
s=p.read_text(encoding="utf-8")
for old,new in [
    ("integral_mp24_at_start","integral_16q_at_start"),
    ("24 leitores qualificados","16 leitores qualificados"),
    ("qualificados='+str(spot.get('qualified',0))+'/24","qualificados='+str(spot.get('qualified',0))+'/16"),
    ("REQUESTED=24","REQUESTED=16"),
    ("24/24 continuous market coverage","16/16 continuous market coverage"),
    ("ROOT/'UNIVERSE_A2.json'","ROOT/'UNIVERSE_A5_16Q.json'"),
    ("target-belial-a4","target-belial-a5-16q"),
    ("belial_a4_result_v1","belial_a5_16q_result_v1"),
    ("A4 PREPARAÇÃO","A5 PREPARAÇÃO"),
    ("BELIAL_A4_CAPTURE_STARTED","BELIAL_A5_CAPTURE_STARTED"),
    ("A4 | restante=","A5 | restante="),
    ("A4 FINALIZAÇÃO","A5 FINALIZAÇÃO"),
]:
    s=s.replace(old,new)

# Preserve mutual exclusion against both old A4 and new A5 supervisors.
s=s.replace(
    "or b'a5.supervisor' in raw:raise SystemExit",
    "or b'a4.supervisor' in raw or b'a5.supervisor' in raw:raise SystemExit",
)
if "b'a4.supervisor' in raw" not in s or "b'a5.supervisor' in raw" not in s:
    raise SystemExit("A5_ACTIVE_SUPERVISOR_GUARD_INCOMPLETE")
p.write_text(s,encoding="utf-8")

# ws_relay.py: only subscription/pool caps.
p=root/"a5/ws_relay.py"
s=p.read_text(encoding="utf-8")
s=s.replace("len(self.pools)>24","len(self.pools)>16")
s=s.replace("WS_ONE_TO_24_IDENTIFIED_POOLS","WS_ONE_TO_16_IDENTIFIED_POOLS")
s=s.replace("len(client.subs)>=24","len(client.subs)>=16")
p.write_text(s,encoding="utf-8")

# dashboard
p=root/"a5/web/dashboard.html"
s=p.read_text(encoding="utf-8")
old_exp="const EXP=['JTO','RENDER','W','TNSR','DRIFT','TRUMP','MELANIA','ORCA','HNT','MEW','MPLX','KMNO'];"
new_exp="const EXP=['RENDER','KMNO','MEW','MPLX'];"
if old_exp not in s:raise SystemExit("DASHBOARD_EXPANSION_ARRAY_MARKER_NOT_FOUND")
s=s.replace(old_exp,new_exp)
for old,new in [
    ("A4 · RESEARCH TERMINAL","A5 · 16Q RESEARCH TERMINAL"),
    ("24 ativos solicitados","16 ativos solicitados"),
    ("24 candidatos","16 candidatos"),
    (">24</strong>",">16</strong>"),
    ("24 FONTES / CAPACIDADES POR ATIVO","16 FONTES / CAPACIDADES POR ATIVO"),
    ("Não equivale a 24 aptos","Não equivale a 16 aptos"),
    ("EXPANSÃO 12","FOCO 4"),
    ("??24","??16"),
    ("D.state.shadow_expansion","D.state.shadow_focus"),
]:
    s=s.replace(old,new)
p.write_text(s,encoding="utf-8")

# engine_a5/main.rs
p=root/"engine_a5/src/main.rs"
s=p.read_text(encoding="utf-8")

old_symbols='const POOL_SYMBOLS: [&str;24] = ["FARTCOIN", "BOME", "WIF", "POPCAT", "RAY", "JUP", "BONK", "PENGU", "PUMP", "USELESS", "GRASS", "PYTH","JTO","RENDER","W","TNSR","DRIFT","TRUMP","MELANIA","ORCA","HNT","MEW","MPLX","KMNO"];'
new_symbols='const POOL_SYMBOLS: [&str;16] = ["FARTCOIN", "BOME", "WIF", "POPCAT", "RAY", "JUP", "BONK", "PENGU", "PUMP", "USELESS", "GRASS", "PYTH", "RENDER", "KMNO", "MEW", "MPLX"];'
if old_symbols not in s:raise SystemExit("RUST_POOL_SYMBOLS_MARKER_NOT_FOUND")
s=s.replace(old_symbols,new_symbols,1)

mint_block = """const A1_MINTS: [&str;16] = [
 "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump","ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82",
 "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm","7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
 "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R","JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
 "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263","2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv",
 "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn","Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk",
 "Grass7B4RdKfBCjTKgSqnXkqjwiGvQyFbuSCUJr3XXjs","HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
 "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof","KMNo3nJsBXfcpJTVhZcXLW7RmTwTt4GVFE7suUBo9sS",
 "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5","METAewgxyPbgwsseH8T16a39CQ5VyVxZi9zXiDPY18m"];"""

s,n=re.subn(r'const A1_MINTS: \[&str;24\] = \[.*?\];',mint_block,s,count=1,flags=re.S)
if n!=1:raise SystemExit("RUST_MINT_BLOCK_PATCH_FAILED")

pairs=[
    ("A2_MP24_ISOLATED_V1","A5_16Q_ISOLATED_V1"),
    ("let count=if isolated {1} else {24};","let count=if isolated {1} else {16};"),
    ("fn a2_universe_twentyfour_unique_fixtures_no_network()","fn a5_universe_sixteen_unique_fixtures_no_network()"),
    ("let cfgs=pools();assert_eq!(cfgs.len(),24);","let cfgs=pools();assert_eq!(cfgs.len(),16);"),
    ("assert_eq!(cfgs.iter().map(|c|c.pool.clone()).collect::<HashSet<_>>().len(),24);","assert_eq!(cfgs.iter().map(|c|c.pool.clone()).collect::<HashSet<_>>().len(),16);"),
    ("assert_eq!(cfgs.iter().map(|c|c.meme_mint.clone()).collect::<HashSet<_>>().len(),24);","assert_eq!(cfgs.iter().map(|c|c.meme_mint.clone()).collect::<HashSet<_>>().len(),16);"),
    ("fn full_scope_requires_twentyfour()","fn full_scope_requires_sixteen()"),
    ("for i in 0..24 {assert!(check_source_scope(&[cfg(i)],true).is_ok());}","for i in 0..16 {assert!(check_source_scope(&[cfg(i)],true).is_ok());}"),
    ("let all=(0..24).map(cfg).collect::<Vec<_>>();","let all=(0..16).map(cfg).collect::<Vec<_>>();"),
    ("#[test] fn exact_24_symbols() { assert_eq!(POOL_SYMBOLS.len(),24); }","#[test] fn exact_16_symbols() { assert_eq!(POOL_SYMBOLS.len(),16); }"),
    ("#[test] fn exact_24_mints() { assert_eq!(A1_MINTS.len(),24); }","#[test] fn exact_16_mints() { assert_eq!(A1_MINTS.len(),16); }"),
    ("assert_eq!(POOL_SYMBOLS.into_iter().collect::<HashSet<_>>().len(),24);","assert_eq!(POOL_SYMBOLS.into_iter().collect::<HashSet<_>>().len(),16);"),
    ("assert_eq!(A1_MINTS.into_iter().collect::<HashSet<_>>().len(),24);","assert_eq!(A1_MINTS.into_iter().collect::<HashSet<_>>().len(),16);"),
    ('symbol:"KMNO".into(),meme_mint:A1_MINTS[23].into()','symbol:"KMNO".into(),meme_mint:A1_MINTS[13].into()'),
]
for old,new in pairs:
    if old not in s:raise SystemExit("RUST_MAIN_MARKER_NOT_FOUND:"+old[:90])
    s=s.replace(old,new,1)

p.write_text(s,encoding="utf-8")

# engine_a5 RAW cap -> 16.
# A4 FIX4 canonico usa 24; builds historicos anteriores usaram 12.
# Aceitamos somente esses dois baselines conhecidos e exigimos uma unica troca.
p=root/"engine_a5/src/bin/belial-spot-raw.rs"
s=p.read_text(encoding="utf-8")
raw_pattern=re.compile(
    r'if\s+rows\.len\(\)\s*>\s*(?:12|24)\s*\{\s*return\s+Err\("RAW_MAX_(?:TWELVE|TWENTY_FOUR)"\.into\(\)\);\s*\}'
)
s,n=raw_pattern.subn('if rows.len()>16 {return Err("RAW_MAX_SIXTEEN".into());}',s,count=1)
if n!=1:raise SystemExit("RAW_CAP_BASELINE_NOT_RECOGNIZED")
p.write_text(s,encoding="utf-8")

# Adapt tests_a5 namespace/contracts.
for p in sorted((root/"tests_a5").rglob("*.py")):
    s=p.read_text(encoding="utf-8")
    s=s.replace("a4.","a5.")
    s=s.replace("tests_a4/paper_harness_a4.py","tests_a5/paper_harness_a5.py")
    s=s.replace("EXPANSION12","FOCUS4")
    s=s.replace("shadow_expansion","shadow_focus")
    s=s.replace("ROOT/'a4/","ROOT/'a5/")
    s=s.replace("ROOT/'engine_a4/","ROOT/'engine_a5/")
    s=s.replace("PRESERVATION_A4.json","PRESERVATION_A5.json")
    s=s.replace("A4_AGENT_FAILED","A5_AGENT_FAILED")
    s=s.replace("self.assertEqual(platform['coverage']['requested'],24)","self.assertEqual(platform['coverage']['requested'],16)")
    s=s.replace("self.assertEqual(len(SYMBOLS),24)","self.assertEqual(len(SYMBOLS),16)")
    p.write_text(s,encoding="utf-8")

# A5-specific test harness.
#
# The inherited A2/A4 accounting Harness uses JTO as its second symbol.
# JTO is intentionally NOT in A5 16Q. Replaying that fixture through the A5
# Controller correctly raises MARKET_UNIVERSE_MISMATCH. Therefore A5 gets its
# own synthetic harness with the same accounting semantics but RENDER as FOCUS4.
p=root/"tests_a5/test_terminal.py"
s=p.read_text(encoding="utf-8")

import_marker="import test_a2_core as old\n"
if import_marker not in s:
    raise SystemExit("A5_TEST_TERMINAL_OLD_IMPORT_MARKER_NOT_FOUND")

helper = """import test_a2_core as old
from a5.catalog import initial as a5_initial, freeze as a5_freeze
from a5.common import cohort_of as a5_cohort_of, DURATION_MS as A5_DURATION_MS

class A5Harness:
    def __init__(self,t=1_000_000,emit=None,cfg=None):
        self.t=t;self.seq=0;self.c=Controller(copy.deepcopy(cfg or old.CFG),emit);self.events=[]
        self.event('markets',{'venue':'hyperliquid','markets':[old.meta(),old.renamed(old.meta(),'RENDER')]},t)
        caps=a5_initial()
        for symbol in ('FARTCOIN','RENDER'):
            caps[symbol].update(qualified=True,state='QUALIFIED',identity=True,price=True,actor=True)
        caps['PUMP'].update(state='RAW_ONLY',qualified=False,reason='TransferHook')
        self.event('cohort',{'cohort':a5_freeze(caps,t),'started_ms':t,'duration_ms':A5_DURATION_MS},t)
        for symbol in ('FARTCOIN','RENDER'):
            self.healthy(symbol,t)
            self.event('book',old.renamed(old.hl(t),symbol),t)
    def event(self,kind,body,t=None):
        self.seq+=1
        e=dict(kind=kind,body=body,at_ms=t if t is not None else self.t,source='fixture',epoch=0,sequence=self.seq)
        self.events.append(copy.deepcopy(e));self.c.apply(e);return e
    def healthy(self,symbol,t,generation=0):
        return self.event('source_status',dict(symbol=symbol,status=dict(
            state='ACTIVE',at_ms=t,generation=generation,actor_ready=True,
            ready_since_ms=t,integrity=True)),t)
    def open(self,symbol='FARTCOIN',t=None):
        t=t or self.t
        self.healthy(symbol,t)
        self.event('book',old.renamed(old.hl(t),symbol),t)
        self.event('signal',old.renamed(old.signal(t,symbol+'-EP'),symbol),t)
        self.event('book',old.renamed(old.hl(t+150),symbol),t+150)
        return old.chosen(self.c.engines[a5_cohort_of(symbol)])
"""
s=s.replace(import_marker,helper,1)

old_harness="""    def harness(self):
        with mock.patch.object(old,'Controller',Controller):return old.Harness()
"""
new_harness="""    def harness(self):
        return A5Harness()
"""
if old_harness not in s:
    raise SystemExit("A5_ACCOUNTING_HARNESS_MARKER_NOT_FOUND")
s=s.replace(old_harness,new_harness,1)

old_display="""        h=old.Harness(emit=lambda k,r:prior.append((k,r)));h.open();h.event('book',old.hl(h.t+250,102,102.01),h.t+250);h.event('book',old.hl(h.t+450,102,102.01),h.t+450)
"""
new_display="""        h=A5Harness(emit=lambda k,r:prior.append((k,r)));h.open();h.event('book',old.hl(h.t+250,102,102.01),h.t+250);h.event('book',old.hl(h.t+450,102,102.01),h.t+450)
"""
if old_display not in s:
    raise SystemExit("A5_DISPLAY_HARNESS_MARKER_NOT_FOUND")
s=s.replace(old_display,new_display,1)

p.write_text(s,encoding="utf-8")

# A5-local deterministic venue fixture.
# It is deliberately stdlib-only: no a5 imports, no legacy test module imports,
# no network, no filesystem dependency outside the supplied session directory.
fixture_path=root/"tests_a5/fixture_venue_a5.py"
fixture_path.write_text('"""Deterministic A5 IPC fixture. Stdlib-only. Never contacts a network."""\nimport argparse\nimport json\nimport os\nimport signal\nimport sys\nimport threading\nimport time\nfrom pathlib import Path\n\nap=argparse.ArgumentParser()\nap.add_argument(\'--venue\',choices=(\'hyperliquid\',\'lighter\'),required=True)\nap.add_argument(\'--session\',type=Path,required=True)\nap.add_argument(\'--binary\')\nap.add_argument(\'--oneshot\',action=\'store_true\')\na=ap.parse_args()\n\nstop=threading.Event()\nreconnect=threading.Event()\ngeneration=1\n\ndef now_ms():\n    return time.time_ns()//1_000_000\n\ndef write_status(state,**extra):\n    path=a.session/\'venues\'/a.venue/\'status.json\'\n    path.parent.mkdir(parents=True,exist_ok=True)\n    tmp=path.with_name(path.name+\'.tmp-\'+str(os.getpid()))\n    tmp.write_text(json.dumps(dict(state=state,venue=a.venue,fixture_only=True,orders=False,**extra),sort_keys=True)+\'\\n\',encoding=\'utf-8\')\n    os.replace(tmp,path)\n\ndef emit(kind,body):\n    sys.stdout.write(json.dumps(dict(source=a.venue,epoch=0,sequence=emit.seq,kind=kind,body=body),separators=(\',\',\':\'))+\'\\n\')\n    sys.stdout.flush()\n    emit.seq+=1\nemit.seq=1\n\ndef market():\n    return dict(\n        venue=a.venue,\n        symbol=\'FARTCOIN\',\n        market=\'FARTCOIN\' if a.venue==\'hyperliquid\' else \'42\',\n        multiplier=1,\n        size_step=.001,\n        min_size=.001,\n        min_notional=10.0,\n        status=\'ACTIVE\',\n    )\n\ndef hyper_book(t,gen):\n    return dict(\n        venue=\'hyperliquid\',\n        generation=gen,\n        received_ns=time.time_ns(),\n        data=dict(\n            channel=\'l2Book\',\n            data=dict(\n                coin=\'FARTCOIN\',\n                time=t,\n                levels=[\n                    [dict(px=\'100\',sz=\'10000\')],\n                    [dict(px=\'100.01\',sz=\'10000\')],\n                ],\n            ),\n        ),\n    )\n\ndef lighter_book(t,nonce,begin,snapshot,gen):\n    return dict(\n        venue=\'lighter\',\n        generation=gen,\n        received_ns=time.time_ns(),\n        data=dict(\n            channel=\'order_book:42\',\n            type=\'subscribed/order_book\' if snapshot else \'update/order_book\',\n            timestamp=t,\n            order_book=dict(\n                nonce=nonce,\n                begin_nonce=begin,\n                last_updated_at=t*1000,\n                bids=[dict(price=\'100\',size=\'10000\')],\n                asks=[dict(price=\'100.01\',size=\'10000\')],\n            ),\n        ),\n    )\n\ndef signal_stop(*_):\n    stop.set()\n\nsignal.signal(signal.SIGTERM,signal_stop)\nsignal.signal(signal.SIGINT,signal_stop)\n\ndef controls():\n    for line in sys.stdin:\n        try:\n            row=json.loads(line)\n        except Exception:\n            continue\n        if row.get(\'stop\') is True:\n            stop.set()\n            return\n        if row.get(\'reconnect\')==a.venue:\n            reconnect.set()\n\nthreading.Thread(target=controls,name=\'a5-fixture-controls\',daemon=True).start()\n\nwrite_status(\'FIXTURE_READY\')\nemit(\'markets\',dict(venue=a.venue,markets=[market()]))\n\ni=0\nnonce=0\nsnapshot=True\nwhile not stop.is_set():\n    i+=1\n    t=now_ms()\n    if reconnect.is_set():\n        reconnect.clear()\n        generation+=1\n        nonce=0\n        snapshot=True\n\n    if a.venue==\'hyperliquid\':\n        body=hyper_book(t,generation)\n    else:\n        nonce+=1\n        begin=None if snapshot else nonce-1\n        # One deliberate gap exercises quarantine/reconnect behavior.\n        if i==20 and generation==1:\n            nonce+=2\n            begin=nonce-1\n        body=lighter_book(t,nonce,begin,snapshot,generation)\n        snapshot=False\n\n    emit(\'book\',body)\n    write_status(\'FIXTURE_STREAMING\',generation=generation,frames=i)\n\n    if a.oneshot and i>=3:\n        break\n    stop.wait(.012)\n\nwrite_status(\'FIXTURE_STOPPED\',generation=generation,frames=i)\n',encoding="utf-8")
fixture_text=fixture_path.read_text(encoding="utf-8")
compile(fixture_text,str(fixture_path),"exec")
if fixture_text.splitlines()[0] != '"""Deterministic A5 IPC fixture. Stdlib-only. Never contacts a network."""':
    raise SystemExit("A5_FIXTURE_SERIALIZATION_FAILED")
print("A5_FIXTURE_GENERATED_SYNTAX=PASS")

# A5 paper harness: explicit fixture command factory.
p=root/"tests_a5/paper_harness_a5.py"
p.write_text("""\"\"\"A5 production PAPER worker exercised with deterministic local IPC peers.\"\"\"
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import a5.paper_worker as worker
from a5.common import ROOT

FIXTURE=ROOT/'tests_a5/fixture_venue_a5.py'

def fixture_command(module,*args):
    if module!='venue_worker':
        raise RuntimeError('TEST_FIXTURE_UNEXPECTED_MODULE:'+str(module))
    if not FIXTURE.is_file():
        raise RuntimeError('A5_FIXTURE_MISSING:'+str(FIXTURE))
    return [sys.executable,'-B',str(FIXTURE),*map(str,args)]

worker.command=fixture_command
worker.public_json=lambda *args:[]
raise SystemExit(worker.run(Path(sys.argv[1]),FIXTURE))
""",encoding="utf-8")

# A5 process tests are generated cleanly rather than patched from A4.
# This removes brittle exact-text transforms and makes the process contract
# explicit for the 16Q runtime.
process_test_path=root/"tests_a5/test_processes.py"
process_test_path.write_text('"""A5 process-isolation tests built for the 16Q runtime.\n\nThese tests do not rewrite A2/A4 fixtures. They exercise A5 PAPER/UI processes\nthrough a deterministic local IPC peer and fail fast with child logs.\n"""\nimport json\nimport os\nimport subprocess\nimport sys\nimport tempfile\nimport time\nimport unittest\nimport urllib.request\nfrom pathlib import Path\n\nfrom a5.common import (\n    ROOT, atomic, read_json, now_ms, DURATION_MS, terminate,\n    command, clean_env, heartbeat, digest,\n)\nfrom a5.catalog import initial, freeze\nfrom a5.replay import replay\nfrom a5.supervisor import Supervisor, package_result\n\n\ndef wait(fn, seconds=10, process=None, log=None, label=\'condition\'):\n    deadline=time.monotonic()+seconds\n    while time.monotonic()<deadline:\n        value=fn()\n        if value:\n            return value\n        if process is not None and process.poll() is not None:\n            details=\'\'\n            if log is not None and Path(log).exists():\n                details=Path(log).read_text(errors=\'replace\')[-12000:]\n            raise AssertionError(\n                \'PROCESS_EXITED_BEFORE_\'+label+\n                \' rc=\'+str(process.returncode)+\'\\n\'+details\n            )\n        time.sleep(.04)\n    details=\'\'\n    if log is not None and Path(log).exists():\n        details=Path(log).read_text(errors=\'replace\')[-12000:]\n    raise AssertionError(\'TIMEOUT_WAITING_FOR_\'+label+\'\\n\'+details)\n\n\nclass ProcessIsolation(unittest.TestCase):\n    def setUp(self):\n        self.previous=os.environ.get(\'BELIAL_A1_NO_BROWSER\')\n        os.environ[\'BELIAL_A1_NO_BROWSER\']=\'1\'\n\n    def tearDown(self):\n        if self.previous is None:\n            os.environ.pop(\'BELIAL_A1_NO_BROWSER\',None)\n        else:\n            os.environ[\'BELIAL_A1_NO_BROWSER\']=self.previous\n\n    def test_paper_continues_across_ui_restart_and_blocked_spot_signal(self):\n        with tempfile.TemporaryDirectory() as td:\n            session=Path(td)/\'a5-process-fixture\'\n            session.mkdir()\n            for folder in (\'shadow_compare\',\'shadow_focus\'):\n                path=session/\'aggregate\'/folder\n                path.mkdir(parents=True)\n                (path/\'events.jsonl\').touch()\n            atomic(session/\'SESSION.json\',dict(\n                phase=\'PREPARING\',\n                duration_ms=DURATION_MS,\n            ))\n            atomic(session/\'INTEGRITY.json\',{\'ok\':True})\n\n            procs=[]\n            logs=[]\n\n            def launch(argv,name):\n                log_path=session/name\n                handle=log_path.open(\'w\')\n                logs.append(handle)\n                proc=subprocess.Popen(\n                    argv,\n                    stdout=handle,\n                    stderr=handle,\n                    cwd=ROOT,\n                    env=clean_env(),\n                    start_new_session=True,\n                )\n                procs.append(proc)\n                return proc,log_path\n\n            paper,paper_log=launch(\n                [sys.executable,\'-B\',str(ROOT/\'tests_a5/paper_harness_a5.py\'),str(session)],\n                \'paper-test.log\',\n            )\n            ui=None\n            ui2=None\n            try:\n                wait(\n                    lambda: read_json(session/\'paper/status.json\',{}).get(\'metadata_finished\'),\n                    process=paper,\n                    log=paper_log,\n                    label=\'PAPER_METADATA\',\n                )\n\n                first_books=wait(\n                    lambda: (\n                        read_json(session/\'paper/books.json\',{})\n                        if read_json(session/\'paper/books.json\',{}).get(\'ingest_messages\',0)>=8\n                        else None\n                    ),\n                    process=paper,\n                    log=paper_log,\n                    label=\'PAPER_BOOKS\',\n                )\n                self.assertIn(\'hyperliquid\',first_books.get(\'venue_runtime\',{}))\n                self.assertIn(\'lighter\',first_books.get(\'venue_runtime\',{}))\n\n                ui,ui_log=launch(\n                    command(\'ui_worker\',\'--session\',session),\n                    \'ui-test.log\',\n                )\n                url=wait(\n                    lambda: read_json(session/\'ui/URL.json\',{}).get(\'url\'),\n                    process=ui,\n                    log=ui_log,\n                    label=\'UI_URL\',\n                )\n                with urllib.request.urlopen(url+\'health\',timeout=2) as response:\n                    health=json.load(response)\n                self.assertTrue(health[\'ok\'])\n                self.assertTrue(health[\'read_only\'])\n                self.assertFalse(health[\'orders\'])\n\n                before=read_json(session/\'paper/books.json\',{}).get(\'ingest_messages\',0)\n                terminate(ui,3)\n                wait(\n                    lambda: read_json(session/\'paper/books.json\',{}).get(\'ingest_messages\',0)>before+10,\n                    process=paper,\n                    log=paper_log,\n                    label=\'PAPER_AFTER_UI_STOP\',\n                )\n                self.assertIsNone(paper.poll())\n\n                ui2,ui2_log=launch(\n                    command(\'ui_worker\',\'--session\',session),\n                    \'ui-restart.log\',\n                )\n                restarted=wait(\n                    lambda: (\n                        read_json(session/\'ui/URL.json\',{})\n                        if read_json(session/\'ui/URL.json\',{}).get(\'pid\')==ui2.pid\n                        else None\n                    ),\n                    process=ui2,\n                    log=ui2_log,\n                    label=\'UI_RESTART\',\n                )\n                with urllib.request.urlopen(restarted[\'url\']+\'health\',timeout=2) as response:\n                    self.assertTrue(json.load(response)[\'ok\'])\n\n                caps=initial()\n                caps[\'FARTCOIN\'].update(\n                    qualified=True,state=\'QUALIFIED\',\n                    identity=True,price=True,actor=True,\n                )\n                caps[\'PUMP\'].update(\n                    state=\'RAW_ONLY\',qualified=False,\n                    reason=\'TransferHook\',\n                )\n                t=now_ms()\n                atomic(session/\'COHORT.json\',freeze(caps,t))\n                heartbeat(session/\'aggregate/status.json\',\'ACTIVE\')\n                heartbeat(\n                    session/\'sources/FARTCOIN/status.json\',\n                    \'ACTIVE\',\n                    generation=0,\n                    integrity=True,\n                    actor_ready=True,\n                    actor_epoch=0,\n                    ready_since_ms=t,\n                )\n                heartbeat(\n                    session/\'sources/PUMP/status.json\',\n                    \'QUARANTINED\',\n                    generation=0,\n                    integrity=False,\n                    reason=\'TransferHook\',\n                )\n                atomic(session/\'SESSION.json\',dict(\n                    phase=\'CAPTURING\',\n                    duration_ms=DURATION_MS,\n                    started_ms=t,\n                    deadline_ms=t+DURATION_MS,\n                    deadline_monotonic_ns=time.monotonic_ns()+8_000_000_000,\n                ))\n\n                wait(\n                    lambda: read_json(session/\'paper/state.json\',{}).get(\'started_ms\'),\n                    process=paper,\n                    log=paper_log,\n                    label=\'PAPER_CAPTURE_START\',\n                )\n\n                event=dict(\n                    kind=\'EPISODE_OPEN\',\n                    episode_id=\'PUMP-A5-FIXTURE\',\n                    symbol=\'PUMP\',\n                    at_unix_ms=now_ms(),\n                    paper_only=True,\n                    orders=False,\n                )\n                with (session/\'aggregate/shadow_compare/events.jsonl\').open(\'a\') as handle:\n                    handle.write(json.dumps(event,separators=(\',\',\':\'))+\'\\n\')\n\n                wait(\n                    lambda: read_json(session/\'paper/state.json\',{}).get(\'a1_blocked_signals\',0)>0,\n                    process=paper,\n                    log=paper_log,\n                    label=\'BLOCKED_SIGNAL\',\n                )\n                self.assertFalse(\n                    read_json(session/\'paper/state.json\')[\'groups\'][\'CORE12\'][\'positions\']\n                )\n\n                state=read_json(session/\'SESSION.json\')\n                state[\'phase\']=\'STOPPING\'\n                atomic(session/\'SESSION.json\',state)\n\n                paper.wait(timeout=12)\n                self.assertEqual(\n                    paper.returncode,0,\n                    paper_log.read_text(errors=\'replace\')[-12000:],\n                )\n                self.assertTrue(read_json(session/\'paper/COMPLETE.json\')[\'ok\'])\n\n                result=replay(\n                    session/\'paper/inputs.jsonl\',\n                    session/\'replay\',\n                )\n                self.assertEqual(\n                    result[\'output_sha256\'],\n                    digest(session/\'paper/outputs.jsonl\'),\n                )\n            finally:\n                for proc in procs:\n                    terminate(proc,3)\n                for handle in logs:\n                    handle.close()\n\n    def test_supervisor_ui_restart_requires_no_pool_approval(self):\n        with tempfile.TemporaryDirectory() as td:\n            session=Path(td)\n            sup=Supervisor(session,base=session)\n            try:\n                sup.setup()\n                wait(\n                    lambda: read_json(session/\'ui/URL.json\',{}),\n                    process=sup.procs[\'ui\'],\n                    log=session/\'logs/ui.log\',\n                    label=\'SUPERVISOR_UI_URL\',\n                )\n                first=sup.procs[\'ui\'].pid\n                terminate(sup.procs[\'ui\'],3)\n                platform=sup.publish()\n                self.assertEqual(platform[\'coverage\'][\'qualified_at_start\'],0)\n                self.assertEqual(platform[\'coverage\'][\'requested\'],16)\n                self.assertNotEqual(sup.procs[\'ui\'].pid,first)\n                wait(\n                    lambda: read_json(session/\'ui/URL.json\',{}).get(\'pid\')==sup.procs[\'ui\'].pid,\n                    process=sup.procs[\'ui\'],\n                    log=session/\'logs/ui.log\',\n                    label=\'SUPERVISOR_UI_RESTART\',\n                )\n            finally:\n                for proc in sup.procs.values():\n                    terminate(proc,3)\n                for handle in sup.logs.values():\n                    handle.close()\n\n    def test_archive_inventories_exported_bytes(self):\n        import hashlib\n        import zipfile\n        with tempfile.TemporaryDirectory() as td:\n            session=Path(td)/\'a5-archive-test\'\n            session.mkdir()\n            atomic(session/\'a.json\',{\'orders\':False})\n            zpath=package_result(session,Path(td)/\'out\')\n            with zipfile.ZipFile(zpath) as archive:\n                inventory=json.loads(archive.read(\'ZIP_SHA256.json\'))\n                for name,expected in inventory.items():\n                    self.assertEqual(\n                        hashlib.sha256(archive.read(name)).hexdigest(),\n                        expected,\n                    )\n\n\nif __name__==\'__main__\':\n    unittest.main()\n',encoding="utf-8")
process_test_text=process_test_path.read_text(encoding="utf-8")
compile(process_test_text,str(process_test_path),"exec")
if "A5_PROCESS_START_ORDER_PATCH_FAILED" in process_test_text:
    raise SystemExit("A5_PROCESS_TEST_CONTAINS_LEGACY_PATCH_MARKER")
print("A5_PROCESS_TEST_REBUILT_CLEAN=PASS")

# Regression guard:
# - terminal accounting harness must use an A5-approved FOCUS4 symbol;
# - retired assets must not survive as exact quoted symbol literals.
#
# Never use substring matching here. "WALL_CLOCK_DRIFT" is an integrity reason,
# not the retired DRIFT asset.
terminal=(root/"tests_a5/test_terminal.py").read_text(encoding="utf-8")
if "class A5Harness:" not in terminal or "'RENDER'" not in terminal:
    raise SystemExit("A5_HARNESS_NOT_INSTALLED")
if "old.Harness(" in terminal:
    raise SystemExit("A5_OLD_HARNESS_REFERENCE_REMAINS")

quoted_retired=re.compile(
    r"""(?P<q>['"])(?:JTO|W|TNSR|DRIFT|TRUMP|MELANIA|ORCA|HNT)(?P=q)"""
)

for runtime_dir in (root/"a5",root/"engine_a5"):
    for q in runtime_dir.rglob("*"):
        if not q.is_file() or q.suffix not in {".py",".rs",".html"}:
            continue
        body=q.read_text(encoding="utf-8")
        match=quoted_retired.search(body)
        if match:
            raise SystemExit(
                "A5_RETIRED_SYMBOL_LITERAL_IN_RUNTIME:"
                +str(q.relative_to(root))+":"+match.group(0)
            )

common=(root/"a5/common.py").read_text(encoding="utf-8")
rust=(root/"engine_a5/src/main.rs").read_text(encoding="utf-8")
dashboard=(root/"a5/web/dashboard.html").read_text(encoding="utf-8")
for symbol in ("RENDER","KMNO","MEW","MPLX"):
    if repr(symbol) not in common and ('"'+symbol+'"') not in common:
        raise SystemExit("A5_FOCUS4_COMMON_SYMBOL_MISSING:"+symbol)
    if ('"'+symbol+'"') not in rust:
        raise SystemExit("A5_FOCUS4_RUST_SYMBOL_MISSING:"+symbol)
    if ("'"+symbol+"'") not in dashboard and ('"'+symbol+'"') not in dashboard:
        raise SystemExit("A5_FOCUS4_DASHBOARD_SYMBOL_MISSING:"+symbol)

print("A5_RETIRED_SYMBOL_LITERAL_SCAN=PASS")
print("A5_TEST_HARNESS_16Q=PASS")
print("A5_PROCESS_FIXTURE_16Q=PASS")

# Native delta test: compare frozen A4 against A5.
# Replace the complete inherited method body and stop at the next known method.
p=root/"tests_a5/test_terminal.py"
s=p.read_text(encoding="utf-8")
pattern=re.compile(
    r"    def test_a4_native_delta_is_only_main_rs\(self\):\n"
    r".*?"
    r"(?=    def test_sensors_use_local_mux_not_direct_remote\(self\):)",
    flags=re.S,
)
replacement = (
"    def test_a5_native_delta_is_only_universe_and_raw_cap(self):\n"
"        a=ROOT/'engine_a4';b=ROOT/'engine_a5'\n"
"        left={str(x.relative_to(a)) for x in a.rglob('*') if x.is_file()};right={str(x.relative_to(b)) for x in b.rglob('*') if x.is_file()}\n"
"        self.assertEqual(left,right)\n"
"        changed=[n for n in sorted(left) if digest(a/n)!=digest(b/n)]\n"
"        self.assertEqual(changed,['src/bin/belial-spot-raw.rs','src/main.rs'])\n"
)
s,n=pattern.subn(replacement,s,count=1)
if n!=1:raise SystemExit("A5_NATIVE_DELTA_TEST_PATCH_FAILED")
p.write_text(s,encoding="utf-8")

# New A5 contract test.
(root/"tests_a5/test_16q_contract.py").write_text("""import json
import unittest
from a5.common import ROOT,CORE12,FOCUS4,SYMBOLS,DURATION_MS,cohort_of

EXPECTED=('FARTCOIN','BOME','WIF','POPCAT','RAY','JUP','BONK','PENGU','PUMP','USELESS','GRASS','PYTH','RENDER','KMNO','MEW','MPLX')
REMOVED={'JTO','W','TNSR','DRIFT','TRUMP','MELANIA','ORCA','HNT'}

class A516QContractTests(unittest.TestCase):
    def test_exact_16q_order(self):
        self.assertEqual(SYMBOLS,EXPECTED)
        self.assertEqual(len(SYMBOLS),16)
        self.assertEqual(len(set(SYMBOLS)),16)
    def test_groups(self):
        self.assertEqual(len(CORE12),12)
        self.assertEqual(FOCUS4,('RENDER','KMNO','MEW','MPLX'))
        self.assertTrue(all(cohort_of(s)=='CORE12' for s in CORE12))
        self.assertTrue(all(cohort_of(s)=='FOCUS4' for s in FOCUS4))
    def test_removed_assets_not_in_a5_universe(self):
        self.assertFalse(REMOVED.intersection(SYMBOLS))
    def test_universe_file(self):
        doc=json.loads((ROOT/'UNIVERSE_A5_16Q.json').read_text())
        self.assertEqual(doc['requested_assets'],16)
        self.assertEqual(tuple(doc['core12']),CORE12)
        self.assertEqual(tuple(x['symbol'] for x in doc['focus4']),FOCUS4)
        self.assertFalse(doc['orders'])
    def test_duration_and_orders_contract(self):
        self.assertEqual(DURATION_MS,5_400_000)
        self.assertFalse(json.loads((ROOT/'UNIVERSE_A5_16Q.json').read_text())['orders'])
    def test_native_scope_is_16(self):
        src=(ROOT/'engine_a5/src/main.rs').read_text()
        self.assertIn('const POOL_SYMBOLS: [&str;16]',src)
        self.assertIn('const A1_MINTS: [&str;16]',src)
        self.assertIn('else {16}',src)
        self.assertNotIn('const POOL_SYMBOLS: [&str;24]',src)
    def test_raw_and_ws_caps_are_16(self):
        raw=(ROOT/'engine_a5/src/bin/belial-spot-raw.rs').read_text()
        ws=(ROOT/'a5/ws_relay.py').read_text()
        self.assertIn('rows.len()>16',raw)
        self.assertIn('RAW_MAX_SIXTEEN',raw)
        self.assertNotIn('RAW_MAX_TWENTY_FOUR',raw)
        self.assertIn('len(self.pools)>16',ws)
        self.assertIn('len(client.subs)>=16',ws)
    def test_dashboard_is_16q(self):
        html=(ROOT/'a5/web/dashboard.html').read_text()
        self.assertIn("const EXP=['RENDER','KMNO','MEW','MPLX'];",html)
        self.assertIn('value="FOCUS4"',html)
        self.assertNotIn('value="EXPANSION12"',html)
        self.assertIn('16 candidatos',html)
    def test_supervisor_refuses_parallel_a4_or_a5(self):
        src=(ROOT/'a5/supervisor.py').read_text()
        self.assertIn("b'a4.supervisor' in raw",src)
        self.assertIn("b'a5.supervisor' in raw",src)
    def test_integrity_reason_drift_is_not_asset_drift(self):
        src=(ROOT/'a5/supervisor.py').read_text()
        self.assertIn('WALL_CLOCK_DRIFT',src)
        self.assertNotIn("'DRIFT'",src)
        self.assertNotIn('"DRIFT"',src)
    def test_a5_test_fixture_is_universe_clean(self):
        terminal=(ROOT/'tests_a5/test_terminal.py').read_text()
        paper=(ROOT/'tests_a5/paper_harness_a5.py').read_text()
        fixture=(ROOT/'tests_a5/fixture_venue_a5.py').read_text()
        self.assertIn('class A5Harness:',terminal)
        self.assertIn("'RENDER'",terminal)
        self.assertNotIn('old.Harness(',terminal)
        self.assertIn("tests_a5/fixture_venue_a5.py",paper)
        self.assertIn('Stdlib-only',fixture)
        self.assertNotIn('from a5.',fixture)
        self.assertNotIn('test_books_paper',fixture)
    def test_a4_remains_present_and_separate(self):
        self.assertTrue((ROOT/'a4/common.py').is_file())
        self.assertTrue((ROOT/'engine_a4/src/main.rs').is_file())
        self.assertNotEqual((ROOT/'a4/common.py').read_text(),(ROOT/'a5/common.py').read_text())
""",encoding="utf-8")


def digest(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):h.update(b)
    return h.hexdigest()

baseline=json.loads((root/"BASELINE_A4_FIX4_MANIFEST_SHA256.json").read_text())
preserved={rel:sha for rel,sha in baseline["files"].items() if (root/rel).is_file()}

preservation={
    "schema":"belial_a5_16q_preservation_v1",
    "base_commit":base_commit,
    "source_release":"belial-a4-90m-v1-fix4",
    "source_manifest_sha256":digest(root/"BASELINE_A4_FIX4_MANIFEST_SHA256.json"),
    "preserved":preserved,
    "intentional_changes":[
        "new a5 Python namespace derived from a4",
        "new engine_a5 derived from engine_a4",
        "new tests_a5 derived from tests_a4 plus 16Q contract tests",
        "A5 canonical universe reduced to 16 assets",
        "engine_a5 full-scope canonical assets reduced 24->16",
        "engine_a5 raw subscription safety cap reduced 24->16",
        "a5 websocket relay cap reduced 24->16",
        "A5 dashboard group EXPANSION12 replaced by FOCUS4",
    ],
    "strategy_thresholds_changed":False,
    "sizing_changed":False,
    "exit_policies_changed":False,
    "duration_ms":5_400_000,
    "orders":False,
    "paper":True,
    "shadow":True,
    "universe":list(SYMBOLS),
}
(root/"docs/PRESERVATION_A5.json").write_text(
    json.dumps(preservation,indent=2,ensure_ascii=False,sort_keys=True)+"\n",
    encoding="utf-8"
)

# Explicit A5 verifier.
(root/"a5/verify.py").write_text('''"""Offline A5 package verification."""
from __future__ import annotations
import argparse,ast,json,os,re,subprocess,sys
from pathlib import Path
from a5.common import ROOT,digest,safe
MANIFEST='MANIFEST_SHA256.json'
GROUPS=('mp12','n2_mp12','d2','runner','perp','a1','a2','n2_a2','labels_a2','a3','a4','a5')

def verify_files(root=ROOT):
    root=safe(root)
    manifest=json.loads((root/MANIFEST).read_text())
    files=manifest.get('files')
    if manifest.get('schema')!='belial_a5_16q_package_v1' or not isinstance(files,dict) or not files:
        raise RuntimeError('INVALID_A5_PACKAGE_MANIFEST')
    actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and str(p.relative_to(root))!=MANIFEST}
    for p in root.rglob('*'):safe(p)
    if actual!=set(files):raise RuntimeError('A5_PACKAGE_MEMBERS_CHANGED:'+str(sorted(actual.symmetric_difference(files))[:20]))
    for relative,expected in files.items():
        p=Path(relative)
        if p.is_absolute() or '..' in p.parts or not re.fullmatch('[0-9a-f]{64}',str(expected)):
            raise RuntimeError('INVALID_A5_MANIFEST_PATH_OR_DIGEST')
        if digest(root/p)!=expected:raise RuntimeError('A5_PACKAGE_HASH_MISMATCH:'+relative)
    return len(files)

def test_suites(root=ROOT,output=None):
    root=Path(root);results={}
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(root),BELIAL_A1_NO_BROWSER='1')
    for group in GROUPS:
        if group in ('a1','a2','labels_a2','a3','a4','a5'):cwd=root
        elif group=='n2_a2':cwd=root/'n2_a2'
        else:cwd=root/'legacy'/group
        if group=='a5':directory=root/'tests_a5'
        elif group=='a4':directory=root/'tests_a4'
        elif group=='a3':directory=root/'tests_a3'
        elif group in ('a1','a2'):directory=root/'tests'
        elif group=='labels_a2':directory=root/'tests_n2_a2'
        else:directory=cwd/'tests'
        argv=[sys.executable,'-B','-m','unittest','discover','-s',str(directory),'-v']
        if group in ('a1','a2'):argv+=['-p','test_'+group+'_*.py']
        p=subprocess.run(argv,cwd=cwd,env=env,capture_output=True,text=True,timeout=240)
        log=p.stdout+p.stderr;match=re.search(r'Ran (\\d+) tests?',log)
        n=int(match.group(1)) if match else 0
        results[group]={'tests':n,'returncode':p.returncode,'passed':p.returncode==0 and n>0,'skips':len(re.findall(r'\\.\\.\\. skipped',log))}
        if output:
            output=Path(output);output.mkdir(parents=True,exist_ok=True);(output/(group+'_tests.log')).write_text(log)
        print('A5_TESTS='+group+'; TESTS='+str(n)+'; PASS='+str(results[group]['passed']).lower(),flush=True)
        if not results[group]['passed'] or results[group]['skips']:
            print(log[-30000:],file=sys.stderr);raise RuntimeError('A5_OFFLINE_SUITE_FAILED:'+group)
    return results

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--hashes-only',action='store_true');ap.add_argument('--log-dir',type=Path);a=ap.parse_args()
    if a.log_dir and (a.log_dir.resolve()==ROOT or ROOT in a.log_dir.resolve().parents):raise SystemExit('Logs devem ficar fora da release selada.')
    n=verify_files();print('A5_PACKAGE_HASHES=PASS; FILES='+str(n),flush=True)
    if not a.hashes_only:
        for f in ROOT.rglob('*.py'):ast.parse(f.read_text(),filename=str(f))
        results=test_suites(output=a.log_dir)
        summary={'ok':True,'tests':sum(v['tests'] for v in results.values()),'groups':results,'native_rust_executed':False,'external_market_test':False,'gpu_executed':False,'orders':False}
        if a.log_dir:(a.log_dir/'VERIFY_RESULT.json').write_text(json.dumps(summary,indent=2))
        print('A5_OFFLINE_VERIFIED='+str(summary['tests'])+'; RUST=NOT_EXECUTED; ORDERS=false',flush=True)
    return 0
if __name__=='__main__':raise SystemExit(main())
''',encoding="utf-8")
PY

echo "[2b/9] Sintaxe de TODO Python A5 gerado + limpeza de artefatos"

python3 - "$WORK" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])

targets=sorted(
    [p for p in (root/"a5").rglob("*.py")]
    + [p for p in (root/"tests_a5").rglob("*.py")]
)
if not targets:
    raise SystemExit("A5_NO_GENERATED_PYTHON_FILES")

for p in targets:
    compile(p.read_text(encoding="utf-8"),str(p),"exec")

fixture=root/"tests_a5/fixture_venue_a5.py"
if not fixture.is_file():
    raise SystemExit("A5_FIXTURE_FILE_MISSING")
fixture_source=fixture.read_text(encoding="utf-8")
fixture_tree=__import__("ast").parse(fixture_source,filename=str(fixture))
fixture_doc=__import__("ast").get_docstring(fixture_tree,clean=False)
expected_fixture_doc="Deterministic A5 IPC fixture. Stdlib-only. Never contacts a network."
if fixture_doc!=expected_fixture_doc:
    raise SystemExit("A5_FIXTURE_DOCSTRING_CONTRACT:"+repr(fixture_doc))

bad=[]
for p in root.rglob("*"):
    rel=p.relative_to(root).as_posix()
    if p.is_symlink():
        bad.append("SYMLINK:"+rel)
    elif p.is_file() and (
        p.suffix in {".pyc",".pyo"}
        or "__pycache__" in p.parts
        or rel.startswith("target/")
        or "/target/" in rel
        or rel.startswith("runtime/")
        or rel.startswith("evidence/")
        or rel.endswith(".tmp")
    ):
        bad.append(rel)

if bad:
    raise SystemExit("A5_GENERATED_ARTIFACTS_REFUSED:"+repr(bad[:40]))

print("A5_GENERATED_PYTHON_FILES="+str(len(targets)))
print("A5_GENERATED_PYTHON_SYNTAX=PASS")
print("A5_FIXTURE_SERIALIZATION=PASS")
print("A5_FIXTURE_DOCSTRING_CONTRACT=PASS")
process_test=(root/"tests_a5/test_processes.py").read_text(encoding="utf-8")
if "A5_PROCESS_START_ORDER_PATCH_FAILED" in process_test:raise SystemExit("A5_PROCESS_TEST_LEGACY_PATCH_MARKER")
if "class ProcessIsolation" not in process_test:raise SystemExit("A5_PROCESS_TEST_CLASS_MISSING")
print("A5_PROCESS_TEST_STRUCTURE=PASS")
print("A5_GENERATED_ARTIFACT_CLEAN=true")
PY

mkdir -p "$EVIDENCE/python-cache"
PYTHONPYCACHEPREFIX="$EVIDENCE/python-cache" \
  python3 -B -m compileall -q -f "$WORK/a5" "$WORK/tests_a5"
echo "A5_PYTHON_COMPILEALL=PASS"

echo "[2c/9] Consistencia interna do bootstrap A5"

python3 - "$WORK" <<'PY'
from pathlib import Path
import ast,sys
root=Path(sys.argv[1])

fixture=root/"tests_a5/fixture_venue_a5.py"
fixture_source=fixture.read_text(encoding="utf-8")
fixture_tree=ast.parse(fixture_source,filename=str(fixture))
fixture_doc=ast.get_docstring(fixture_tree,clean=False)
expected="Deterministic A5 IPC fixture. Stdlib-only. Never contacts a network."
if fixture_doc!=expected:
    raise SystemExit("A5_FIXTURE_SEMANTIC_CONTRACT:"+repr(fixture_doc))

process_test=root/"tests_a5/test_processes.py"
source=process_test.read_text(encoding="utf-8")
tree=ast.parse(source,filename=str(process_test))
classes={n.name for n in tree.body if isinstance(n,ast.ClassDef)}
if "ProcessIsolation" not in classes:
    raise SystemExit("A5_PROCESS_TEST_CLASS_MISSING")

# Generated process test must be A5-native and must not contain the old brittle
# patch machinery from intermediate installers.
for stale in (
    "A5_PROCESS_START_ORDER_PATCH_FAILED",
    "m2-a4-fixture",
    "old_launch=",
    "new_launch=",
):
    if stale in source:
        raise SystemExit("A5_STALE_PROCESS_TEST_TEXT:"+stale)

print("A5_INTERNAL_CONSISTENCY=PASS")
PY

echo "[3/9] Gates estruturais A5 16Q"

python3 - "$WORK" "$SOURCE_CANONICAL" "$EVIDENCE/a5-structural.json" <<'PY'
from pathlib import Path
import hashlib,json,sys
root=Path(sys.argv[1]);source=Path(sys.argv[2]);out=Path(sys.argv[3])

EXPECTED=["FARTCOIN","BOME","WIF","POPCAT","RAY","JUP","BONK","PENGU","PUMP","USELESS","GRASS","PYTH","RENDER","KMNO","MEW","MPLX"]

def sha(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):h.update(b)
    return h.hexdigest()

for rel in [
 "UNIVERSE_A2.json","a4/common.py","a4/supervisor.py","a4/web/dashboard.html",
 "engine_a4/src/main.rs","engine_a4/src/bin/belial-spot-raw.rs","tests_a4/test_terminal.py"
]:
    if sha(root/rel)!=sha(source/rel):raise SystemExit("A4_MUTATED:"+rel)

u=json.loads((root/"UNIVERSE_A5_16Q.json").read_text())
symbols=u["core12"]+[x["symbol"] for x in u["focus4"]]
if symbols!=EXPECTED:raise SystemExit("A5_UNIVERSE_ORDER_MISMATCH")
if u["requested_assets"]!=16 or u["orders"] is not False:raise SystemExit("A5_UNIVERSE_CONTRACT")

common=(root/"a5/common.py").read_text()
if "FOCUS4=('RENDER', 'KMNO', 'MEW', 'MPLX')" not in common:raise SystemExit("A5_FOCUS4_COMMON")
if "VERSION='BELIAL_A5_16Q_V1'" not in common:raise SystemExit("A5_VERSION")

rust=(root/"engine_a5/src/main.rs").read_text()
if 'const POOL_SYMBOLS: [&str;16]' not in rust or 'const A1_MINTS: [&str;16]' not in rust:raise SystemExit("A5_RUST_16Q_SCOPE")
raw=(root/"engine_a5/src/bin/belial-spot-raw.rs").read_text()
if 'rows.len()>16' not in raw or 'RAW_MAX_SIXTEEN' not in raw:raise SystemExit("A5_RAW_CAP")
if 'rows.len()>24' in raw or 'RAW_MAX_TWENTY_FOUR' in raw:raise SystemExit("A5_RAW_OLD_CAP_STILL_PRESENT")
ws=(root/"a5/ws_relay.py").read_text()
if 'len(self.pools)>16' not in ws or 'len(client.subs)>=16' not in ws:raise SystemExit("A5_WS_CAP")

a=root/"engine_a4";b=root/"engine_a5"
left={str(x.relative_to(a)) for x in a.rglob("*") if x.is_file()}
right={str(x.relative_to(b)) for x in b.rglob("*") if x.is_file()}
if left!=right:raise SystemExit("A5_ENGINE_MEMBER_SET_CHANGED")
changed=[n for n in sorted(left) if sha(a/n)!=sha(b/n)]
expected_delta=["src/bin/belial-spot-raw.rs","src/main.rs"]
if changed!=expected_delta:raise SystemExit("A5_NATIVE_DELTA_UNEXPECTED:"+repr(changed))

out.write_text(json.dumps({
 "a4_critical_preserved":True,"universe":EXPECTED,"requested":16,
 "native_delta":changed,"strategy_thresholds_changed":False,
 "sizing_changed":False,"exit_policies_changed":False,"orders":False
},indent=2,sort_keys=True)+"\n")
print("A5_16Q_UNIVERSE=PASS")
print("A5_A4_CRITICAL_PRESERVED=true")
print("A5_NATIVE_DELTA="+repr(changed))
print("A5_ORDERS=false")
print("A5_RETIRED_LITERAL_POLICY=QUOTED_SYMBOLS_ONLY")
PY

echo "[4/9] Selando MANIFEST A5"

python3 - "$WORK" <<'PY'
from pathlib import Path
import hashlib,json,sys
root=Path(sys.argv[1]);manifest=root/"MANIFEST_SHA256.json"
files={}
for p in sorted(root.rglob("*")):
    if not p.is_file():continue
    rel=str(p.relative_to(root))
    if rel=="MANIFEST_SHA256.json":continue
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):h.update(b)
    files[rel]=h.hexdigest()
manifest.write_text(json.dumps({
 "schema":"belial_a5_16q_package_v1",
 "base_release":"belial-a4-90m-v1-fix4",
 "base_commit":"bd554ed580062bcfcd12bb0751ff516e3b8b0faa",
 "files":files,"requested_assets":16,"orders":False
},indent=2,ensure_ascii=False,sort_keys=True)+"\n")
print("A5_MANIFEST_FILES="+str(len(files)))
PY

(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m a5.verify --hashes-only
) | tee "$EVIDENCE/a5-hashes.log"

echo "[4/9b] Preflight estrutural do teste de processos A5"

python3 - "$WORK" <<'PY'
from pathlib import Path
import ast,sys
root=Path(sys.argv[1])
path=root/'tests_a5/test_processes.py'
source=path.read_text(encoding='utf-8')
tree=ast.parse(source,filename=str(path))

classes={node.name:node for node in tree.body if isinstance(node,ast.ClassDef)}
if 'ProcessIsolation' not in classes:
    raise SystemExit('A5_PROCESS_CLASS_NOT_FOUND')

methods={
    node.name for node in classes['ProcessIsolation'].body
    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))
}
expected={
    'test_paper_continues_across_ui_restart_and_blocked_spot_signal',
    'test_supervisor_ui_restart_requires_no_pool_approval',
    'test_archive_inventories_exported_bytes',
}
if not expected.issubset(methods):
    raise SystemExit('A5_PROCESS_METHODS_MISSING:'+repr(sorted(expected-methods)))

for forbidden in (
    'A5_PROCESS_START_ORDER_PATCH_FAILED',
    'old_launch=',
    'new_launch=',
    "m2-a4-fixture",
):
    if forbidden in source:
        raise SystemExit('A5_PROCESS_LEGACY_PATCH_TEXT:'+forbidden)

print('A5_PROCESS_AST_PREFLIGHT=PASS')
PY

echo "[4a/9] Preflight do protocolo IPC A5 deterministico"

python3 - "$WORK" "$EVIDENCE" <<'PY'
from pathlib import Path
import json,subprocess,sys,tempfile

root=Path(sys.argv[1])
evidence=Path(sys.argv[2])
fixture=root/'tests_a5/fixture_venue_a5.py'

for venue in ('hyperliquid','lighter'):
    with tempfile.TemporaryDirectory(prefix='belial-a5-fixture-preflight-') as td:
        session=Path(td)
        p=subprocess.run(
            [sys.executable,'-B',str(fixture),'--venue',venue,'--session',str(session),'--binary','UNUSED','--oneshot'],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=8,
            env={**__import__('os').environ,'PYTHONDONTWRITEBYTECODE':'1'},
        )
        (evidence/f'fixture-{venue}.stdout.log').write_text(p.stdout)
        (evidence/f'fixture-{venue}.stderr.log').write_text(p.stderr)
        if p.returncode!=0:
            raise SystemExit(f'A5_FIXTURE_PREFLIGHT_PROCESS_FAILED:{venue}:rc={p.returncode}:{p.stderr[-2000:]}')
        rows=[]
        for line in p.stdout.splitlines():
            if line.strip():
                rows.append(json.loads(line))
        if len(rows)<4:
            raise SystemExit(f'A5_FIXTURE_PREFLIGHT_TOO_FEW_ROWS:{venue}:{len(rows)}')
        if rows[0].get('kind')!='markets':
            raise SystemExit(f'A5_FIXTURE_PREFLIGHT_FIRST_EVENT:{venue}:{rows[0]}')
        markets=(rows[0].get('body') or {}).get('markets') or []
        if len(markets)!=1 or markets[0].get('symbol')!='FARTCOIN' or markets[0].get('venue')!=venue:
            raise SystemExit(f'A5_FIXTURE_PREFLIGHT_MARKET_CONTRACT:{venue}:{markets}')
        books=[r for r in rows[1:] if r.get('kind')=='book']
        if len(books)<3:
            raise SystemExit(f'A5_FIXTURE_PREFLIGHT_BOOK_COUNT:{venue}:{len(books)}')
        if any((r.get('body') or {}).get('venue')!=venue for r in books):
            raise SystemExit(f'A5_FIXTURE_PREFLIGHT_WRONG_VENUE:{venue}')

print('A5_FIXTURE_PROTOCOL_PREFLIGHT=PASS')
PY

echo "[4b/9] Smoke tests A5 16Q de harness/processo antes da suite completa"

mkdir -p "$EVIDENCE/tests"

(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m unittest -v     tests_a5.test_16q_contract     tests_a5.test_terminal.AccountingTests     tests_a5.test_processes.ProcessIsolation.test_paper_continues_across_ui_restart_and_blocked_spot_signal
) >"$EVIDENCE/tests/a5-16q-smoke.log" 2>&1 || {
  tail -n 300 "$EVIDENCE/tests/a5-16q-smoke.log" >&2
  fail "A5_16Q_TARGETED_SMOKE_FAILED"
}

echo "A5_16Q_TARGETED_SMOKE=PASS"

echo "[5/9] Suites offline A5 + regressao A4"

(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m a5.verify --log-dir "$EVIDENCE/tests"
) | tee "$EVIDENCE/a5-offline-suite.log"

python3 - "$EVIDENCE/tests/VERIFY_RESULT.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
if not d.get("ok"):raise SystemExit("A5_VERIFY_NOT_OK")
for g in ("a4","a5"):
    row=(d.get("groups") or {}).get(g) or {}
    if not row.get("passed"):raise SystemExit("GROUP_NOT_GREEN:"+g)
print("A5_OFFLINE_TOTAL_TESTS="+str(d.get("tests")))
print("A4_REGRESSION_GROUP=PASS")
print("A5_GROUP=PASS")
PY

echo "[6/9] Rust engine_a5 check + test/build offline"

mkdir -p "$TARGET"
export CARGO_TARGET_DIR="$TARGET"
export CARGO_NET_OFFLINE=true
export TOKIO_WORKER_THREADS=1

echo "A5_RUST_CHECK=engine_a5"
cargo check \
  --release \
  --locked \
  --offline \
  --jobs 1 \
  --manifest-path "$WORK/engine_a5/Cargo.toml" \
  >"$EVIDENCE/rust-check.log" 2>&1 || {
    tail -n 220 "$EVIDENCE/rust-check.log" >&2
    fail "A5_RUST_CHECK_FAILED"
  }
echo "A5_RUST_CHECK=PASS"

if command -v cargo >/dev/null 2>&1 && cargo clippy --version >/dev/null 2>&1; then
  echo "A5_RUST_CLIPPY=RUNNING"
  cargo clippy \
    --release \
    --locked \
    --offline \
    --jobs 1 \
    --manifest-path "$WORK/engine_a5/Cargo.toml" \
    >"$EVIDENCE/rust-clippy.log" 2>&1 || {
      tail -n 220 "$EVIDENCE/rust-clippy.log" >&2
      fail "A5_RUST_CLIPPY_FAILED"
    }
  echo "A5_RUST_CLIPPY=PASS"
else
  echo "A5_RUST_CLIPPY=NOT_AVAILABLE"
fi

for BIN in belial-perp-feed belial-spot-raw belial-r5c2-live-profiler; do
  echo "A5_RUST_TEST=$BIN"
  cargo test --release --locked --offline --jobs 1 \
    --manifest-path "$WORK/engine_a5/Cargo.toml" --bin "$BIN" \
    >"$EVIDENCE/rust-test-$BIN.log" 2>&1 || {
      tail -n 200 "$EVIDENCE/rust-test-$BIN.log" >&2
      fail "A5_RUST_TEST_FAILED:$BIN"
    }

  echo "A5_RUST_BUILD=$BIN"
  cargo build --release --locked --offline --jobs 1 \
    --manifest-path "$WORK/engine_a5/Cargo.toml" --bin "$BIN" \
    >"$EVIDENCE/rust-build-$BIN.log" 2>&1 || {
      tail -n 200 "$EVIDENCE/rust-build-$BIN.log" >&2
      fail "A5_RUST_BUILD_FAILED:$BIN"
    }
done

echo "A5_RUST=PASS"

echo "[7/9] Instalando release imutavel SEM iniciar captura"

if [ -e "$DEST" ]; then
  if diff -qr -- "$WORK" "$DEST" >"$EVIDENCE/release-reuse.diff" 2>&1; then
    echo "A5_RELEASE_REUSED_EXACT=true"
  else
    cat "$EVIDENCE/release-reuse.diff" >&2
    fail "EXISTING_A5_RELEASE_DIFFERS"
  fi
else
  STAGING="$BASE/releases/.belial-a5-16q-staging-$TS"
  cp -a "$WORK" "$STAGING"
  (
    cd "$STAGING"
    PYTHONPATH="$STAGING" python3 -B -m a5.verify --hashes-only
  ) >"$EVIDENCE/installed-hash-check.log" 2>&1
  mv "$STAGING" "$DEST"
  echo "A5_RELEASE_INSTALLED=$DEST"
fi

echo "A5_CAPTURE_STARTED=false"

echo "[8/9] Exportando snapshot + handoff + checkpoint Git"

if [ -e "$NEW_SNAPSHOT" ]; then
  if ! diff -qr -- "$WORK" "$NEW_SNAPSHOT" >"$EVIDENCE/git-snapshot-reuse.diff" 2>&1; then
    cat "$EVIDENCE/git-snapshot-reuse.diff" >&2
    fail "EXISTING_A5_GIT_SNAPSHOT_DIFFERS"
  fi
else
  mkdir -p "$(dirname "$NEW_SNAPSHOT")"
  cp -a "$WORK" "$NEW_SNAPSHOT"
fi

mkdir -p "$(dirname "$INSTALLER_DEST")" "$REPO/docs"
cp -- "$SELF" "$INSTALLER_DEST"
chmod 0755 "$INSTALLER_DEST"

PACKAGE_SHA="$(sha256sum "$DEST/MANIFEST_SHA256.json" | awk '{print $1}')"
INSTALLER_SHA="$(sha256sum "$INSTALLER_DEST" | awk '{print $1}')"
A5_MAIN_SHA="$(sha256sum "$DEST/engine_a5/src/main.rs" | awk '{print $1}')"
A5_RAW_SHA="$(sha256sum "$DEST/engine_a5/src/bin/belial-spot-raw.rs" | awk '{print $1}')"

cat > "$REPO/docs/HANDOFF_A5_16Q_BOOTSTRAP_V9.md" <<EOF
# Bot Belial - Handoff A5 16Q Bootstrap

Gerado em **$TS** por \`BELIAL_A5_16Q_BOOTSTRAP_V9_COMPLETO.sh\`.

## Base congelada

\`\`\`text
A4 FIX4 commit: $EXPECTED_HEAD
A4 release: belial-a4-90m-v1-fix4
\`\`\`

O A4 nao foi modificado.

## Universo A5 16Q

\`\`\`text
FARTCOIN BOME WIF POPCAT
RAY JUP BONK PENGU
PUMP USELESS GRASS PYTH
RENDER KMNO MEW MPLX
\`\`\`

## Mudancas intencionais

- namespace Python novo: \`a5/\`;
- engine novo: \`engine_a5/\`;
- testes novos: \`tests_a5/\`;
- universo canonico 24 -> 16 no A5;
- FOCUS4 = RENDER, KMNO, MEW, MPLX;
- limite RAW nativo A5: 16 (A4 FIX4: 24);
- limite WS relay A5: 16;
- dashboard A5 mostra 16 candidatos e grupo FOCUS4.

## Nao alterado

- thresholds de estrategia;
- sizing;
- politicas de saida;
- duracao de 90 minutos;
- PAPER / SHADOW;
- ordens reais continuam desabilitadas.

## Gates

- A4 source hash gate: PASS;
- A4 critical preservation: PASS;
- A5 16Q structural gate: PASS;
- A4 regression tests: PASS;
- A5 16Q targeted smoke: PASS;
- A5 tests: PASS;
- Rust engine_a5 test/build: PASS;
- captura de mercado: NAO INICIADA.

## Hashes

\`\`\`text
A5 MANIFEST  $PACKAGE_SHA
A5 main.rs   $A5_MAIN_SHA
A5 raw.rs    $A5_RAW_SHA
installer    $INSTALLER_SHA
\`\`\`

## Proximo passo

Depois do checkpoint Git deste bootstrap, criar/rodar o gate de sessao
\`BELIAL_A5_16Q_90M\`.
EOF

cat > "$REPO/docs/CURRENT_STATE.md" <<EOF
# Bot Belial - Estado atual

## Linha atual

**BELIAL_A5_16Q bootstrap concluido localmente.**

Base congelada:

\`\`\`text
bd554ed fix(a4): track canonical FIX4 validation members
\`\`\`

A4 FIX4 permanece preservado e separado.

## A5 16Q

\`\`\`text
FARTCOIN BOME WIF POPCAT
RAY JUP BONK PENGU
PUMP USELESS GRASS PYTH
RENDER KMNO MEW MPLX
\`\`\`

- 16 ativos;
- CORE12 + FOCUS4;
- 90 minutos preservados;
- PAPER / SHADOW;
- ordens=false;
- thresholds/sizing/exit policies nao alterados;
- namespace \`a5/\` separado;
- engine \`engine_a5/\` separado;
- testes \`tests_a5/\` separados;
- captura A5 de 90 minutos ainda nao iniciada.

## Proximo gate

Checkpoint Git do bootstrap A5, depois sessao \`BELIAL_A5_16Q_90M\`.
EOF

DEEP_LOG="$REPO/Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv"
if [ ! -f "$DEEP_LOG" ]; then
  printf 'utc\tphase\tstatus\trelease\tmanifest_sha256\tinstaller_sha256\n' > "$DEEP_LOG"
fi
if ! grep -Fq "$RELEASE_NAME" "$DEEP_LOG"; then
  printf '%s\tA5_16Q_BOOTSTRAP\tLOCAL_GATES_PASS\t%s\t%s\t%s\n' \
    "$TS" "$RELEASE_NAME" "$PACKAGE_SHA" "$INSTALLER_SHA" >> "$DEEP_LOG"
fi

CHECKPOINT="$REPO/Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A5_16Q_BOOTSTRAP_V9.sha256"
(
  cd "$REPO"
  sha256sum \
    "$NEW_REL/MANIFEST_SHA256.json" \
    "$NEW_REL/UNIVERSE_A5_16Q.json" \
    "$NEW_REL/a5/common.py" \
    "$NEW_REL/engine_a5/src/main.rs" \
    "$NEW_REL/engine_a5/src/bin/belial-spot-raw.rs" \
    "$INSTALLER_REL" \
    docs/HANDOFF_A5_16Q_BOOTSTRAP_V9.md \
    docs/CURRENT_STATE.md \
    > "$CHECKPOINT"
)

cat > "$EVIDENCE/RESULT.env" <<EOF
BELIAL_A5_16Q_BOOTSTRAP_V9=PASS
UTC=$TS
BASE_COMMIT=$EXPECTED_HEAD
RELEASE=$DEST
SNAPSHOT=$NEW_SNAPSHOT
MANIFEST_SHA256=$PACKAGE_SHA
INSTALLER_SHA256=$INSTALLER_SHA
REQUESTED_ASSETS=16
A4_PRESERVED=true
RUST_PASS=true
PAPER=true
SHADOW=true
ORDERS=false
CAPTURE_STARTED=false
GIT_COMMIT_PERFORMED_AT_RESULT_WRITE=false
EOF

cp "$EVIDENCE/RESULT.env" "$BACKUP/RESULT.env"

echo "[9/9] Staging Git exato - SEM commit/push"

cd "$REPO"

# O pacote canonico contem *.log historicos; force-add evita repetir o defeito
# que havia deixado o snapshot A4 incompleto.
git add -f -- "$NEW_REL"

git add -- \
  "$INSTALLER_REL" \
  "Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv" \
  "Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A5_16Q_BOOTSTRAP_V9.sha256" \
  "docs/HANDOFF_A5_16Q_BOOTSTRAP_V9.md" \
  "docs/CURRENT_STATE.md"

git diff --cached --check

echo
echo "[9b/9] Prova do INDEX A5 que sera commitado"

TREE_SHA="$(git write-tree)"
echo "A5_STAGED_TREE_SHA=$TREE_SHA"

INDEX_EXPORT="$TMP/a5-index-export"
mkdir -p "$INDEX_EXPORT"
git archive --format=tar "$TREE_SHA" -- "$NEW_REL" | tar -xf - -C "$INDEX_EXPORT"

INDEX_SNAPSHOT="$INDEX_EXPORT/$NEW_REL"
[ -d "$INDEX_SNAPSHOT" ] || fail "A5_STAGED_INDEX_EXPORT_MISSING"

(
  cd "$INDEX_SNAPSHOT"
  PYTHONPATH="$INDEX_SNAPSHOT" python3 -B -m a5.verify --hashes-only
) | tee "$EVIDENCE/a5-staged-index-hashes.log"

grep -Fq "A5_PACKAGE_HASHES=PASS" "$EVIDENCE/a5-staged-index-hashes.log" \
  || fail "A5_STAGED_INDEX_HASH_GATE_FAILED"

echo "STAGED_INDEX_A5_CANONICAL=true"

echo
echo "[9c/9] Gate final de escopo staged"

python3 - "$REPO" "$NEW_REL" "$INSTALLER_REL" <<'PY'
from pathlib import Path
import subprocess,sys

repo=Path(sys.argv[1])
snapshot=sys.argv[2]
installer=sys.argv[3]

names=subprocess.check_output(
    ["git","-C",str(repo),"diff","--cached","--name-only"],
    text=True,
).splitlines()

allowed_exact={
    installer,
    "Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv",
    "Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A5_16Q_BOOTSTRAP_V9.sha256",
    "docs/HANDOFF_A5_16Q_BOOTSTRAP_V9.md",
    "docs/CURRENT_STATE.md",
}

bad=[]
snapshot_count=0
prefix=snapshot+"/"
for name in names:
    if name.startswith(prefix):
        snapshot_count+=1
    elif name not in allowed_exact:
        bad.append(name)

if bad:
    print("A5_UNEXPECTED_STAGED_FILES:")
    for x in bad:print(x)
    raise SystemExit(1)

if snapshot_count < 300:
    raise SystemExit("A5_STAGED_SNAPSHOT_SUSPICIOUSLY_SMALL:"+str(snapshot_count))

print("A5_STAGED_SNAPSHOT_FILES="+str(snapshot_count))
print("A5_STAGED_SCOPE_EXACT=true")
PY

echo
echo "=== STAGED STAT ==="
git --no-pager diff --cached --stat

echo
echo "=== STAGED STATUS ==="
git status --short

echo
echo "============================================================"
echo " BELIAL A5 16Q BOOTSTRAP V9 = PASS"
echo "============================================================"
cat "$EVIDENCE/RESULT.env"
echo "STAGED_INDEX_A5_CANONICAL=true"
echo "A5_STAGED_SCOPE_EXACT=true"

echo
echo "[10/10] Commit + push automaticos apos todos os gates verdes"

git commit -m "feat(a5): bootstrap isolated 16Q research line"

NEW_HEAD="$(git rev-parse HEAD)"
echo "A5_COMMIT_SHA=$NEW_HEAD"

if ! git push origin main; then
  echo "A5_GIT_COMMIT_PERFORMED=true"
  echo "A5_GIT_PUSH_PERFORMED=false"
  echo "Commit local preservado. Para repetir somente o push:"
  echo "  cd \"$REPO\" && git push origin main"
  fail "GIT_PUSH_FAILED_AFTER_LOCAL_COMMIT"
fi

git fetch origin main >/dev/null 2>&1 || true
REMOTE_AFTER="$(git rev-parse origin/main)"
[ "$REMOTE_AFTER" = "$NEW_HEAD" ] || fail "REMOTE_HEAD_NOT_EQUAL_LOCAL_AFTER_PUSH"

if [ -n "$(git status --porcelain=v1)" ]; then
  git status --short
  fail "WORKTREE_NOT_CLEAN_AFTER_COMMIT"
fi

cat > "$EVIDENCE/GIT_FINAL.env" <<EOF
A5_GIT_COMMIT_PERFORMED=true
A5_GIT_PUSH_PERFORMED=true
A5_COMMIT_SHA=$NEW_HEAD
ORIGIN_MAIN=$REMOTE_AFTER
WORKTREE_CLEAN=true
CAPTURE_STARTED=false
EOF

echo
echo "============================================================"
echo " BELIAL A5 16Q BOOTSTRAP V9 COMPLETO = FINALIZADO"
echo "============================================================"
cat "$EVIDENCE/GIT_FINAL.env"
echo
echo "ULTIMOS_COMMITS:"
git --no-pager log -4 --oneline --decorate
echo
echo "A5 90M ainda NAO foi iniciada."
