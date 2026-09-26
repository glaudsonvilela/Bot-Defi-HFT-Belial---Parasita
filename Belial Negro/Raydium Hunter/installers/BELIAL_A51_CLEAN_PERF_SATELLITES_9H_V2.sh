#!/usr/bin/env bash
set -euo pipefail
umask 077

# BELIAL A5.1 CLEAN/PERF + SATELLITES SHADOW + 9H V1
#
# Base imutável: A5 16Q + Smoke25 PASS.
#
# Mudanças profundas em linha isolada:
#   - a51/ + engine_a51/ + tests_a51/
#   - duração 540 min / 9 horas
#   - cache JSON por mtime/size nos status hot-path
#   - eliminação de leituras repetidas em paper/supervisor
#   - books.json de display 10 Hz; state/platform 2 Hz
#   - projeção/UI limitada e coalescida; ledger não é alterado
#   - corrige o UI A5 para servir o dashboard da própria linha a51
#   - Binance + Bybit + Kraken: GET público, SHADOW-only, sem chaves
#   - satélites NÃO criam/bloqueiam/dimensionam/encerram trades
#   - Rust: rustfmt + check + clippy + test/build release offline
#
# Depois de TODOS os gates verdes, cria commit/push e inicia a sessão 9H.
# Nenhuma ordem real.

EXPECTED_HEAD="eb1beadeedd0f81f8ab1b882ac86c5fc39332386"

BASE="${BELIAL_BASE:-/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter}"
REPO="${BELIAL_GIT_REPO:-$HOME/Área de trabalho/belial-github-publicar}"

SOURCE_REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a5-16q-v1"
SOURCE="$REPO/$SOURCE_REL"
NEW_REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a51-16q-9h-v1"
NEW_SNAPSHOT="$REPO/$NEW_REL"

INSTALLER_REL="Belial Negro/Raydium Hunter/installers/BELIAL_A51_CLEAN_PERF_SATELLITES_9H_V2.sh"
INSTALLER_DEST="$REPO/$INSTALLER_REL"
RELEASE_NAME="belial-a51-16q-9h-v1"
DEST="$BASE/releases/$RELEASE_NAME"
TARGET="$BASE/target-belial-a51-9h"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE="$BASE/evidence/a51-build-$TS"
SESSION="$BASE/evidence/a51-9h-$TS"
SUP_LOG="$EVIDENCE/a51-9h-supervisor.log"
PERF="$EVIDENCE/a51-9h-performance.jsonl"
FINAL_REPORT="$EVIDENCE/A51_9H_REPORT.json"

SELF="$(readlink -f "$0")"
TMP="$(mktemp -d -t belial-a51-9h-XXXXXX)"
EXPORT="$TMP/git-export"
SOURCE_CANONICAL="$EXPORT/$SOURCE_REL"
WORK="$TMP/BELIAL_A51_16Q_9H_V1"
SUP_PID=""
SESSION_STARTED=0

cleanup_tmp(){ rm -rf -- "$TMP"; }
stop_session(){
  if [ "${SESSION_STARTED:-0}" -eq 1 ] && [ -n "${SUP_PID:-}" ] && kill -0 "$SUP_PID" 2>/dev/null; then
    kill -INT "$SUP_PID" 2>/dev/null || true
  fi
}
trap 'stop_session; cleanup_tmp' INT TERM
trap cleanup_tmp EXIT

fail(){
  echo
  echo "BELIAL_A51_9H_FAILED=$1" >&2
  exit 3
}

if [ "$(id -u)" -eq 0 ]; then fail "DO_NOT_RUN_AS_ROOT"; fi

for cmd in git python3 sha256sum find diff cmp cp mkdir awk grep sed cargo rustfmt findmnt tee tail mv date readlink chmod tar ps kill; do
  command -v "$cmd" >/dev/null || fail "DEPENDENCY_MISSING:$cmd"
done

findmnt -rn --mountpoint /mnt/sherlock-ssd >/dev/null || fail "SSD_NOT_MOUNTED"
[ -d "$BASE" ] || fail "BELIAL_BASE_NOT_FOUND"
[ -d "$REPO/.git" ] || fail "GIT_REPO_NOT_FOUND"
[ -d "$SOURCE" ] || fail "A5_SOURCE_NOT_FOUND"
mkdir -p "$EVIDENCE" "$BASE/releases"

echo "============================================================"
echo " BELIAL A5.1 | CLEAN/PERF + SAT SHADOW | 9 HORAS"
echo "============================================================"
echo "BASE=$BASE"
echo "REPO=$REPO"
echo "SOURCE=$SOURCE"
echo "DEST=$DEST"
echo "SESSION=$SESSION"
echo

# ---------------------------------------------------------------------------
# 0. Git baseline
# ---------------------------------------------------------------------------
echo "[0/12] Baseline Git"
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

# ---------------------------------------------------------------------------
# 1. Canonical A5 source
# ---------------------------------------------------------------------------
echo "[1/12] Export canônico A5 16Q"
mkdir -p "$EXPORT"
(
  cd "$REPO"
  git archive --format=tar HEAD -- "$SOURCE_REL"
) | tar -xf - -C "$EXPORT"
[ -d "$SOURCE_CANONICAL" ] || fail "CANONICAL_A5_EXPORT_NOT_FOUND"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export BELIAL_A1_NO_BROWSER=1

(
  cd "$SOURCE_CANONICAL"
  PYTHONPATH="$SOURCE_CANONICAL" python3 -B -m a5.verify --hashes-only
) | tee "$EVIDENCE/a5-base-hashes.log"
grep -Fq "A5_PACKAGE_HASHES=PASS" "$EVIDENCE/a5-base-hashes.log" || fail "A5_BASE_HASH_GATE"

# ---------------------------------------------------------------------------
# 2. Isolated A5.1 derivation
# ---------------------------------------------------------------------------
echo "[2/12] Derivando A5.1 isolado"
mkdir -p "$WORK"
cp -a "$SOURCE_CANONICAL/." "$WORK/"

python3 - "$WORK" "$EXPECTED_HEAD" <<'PY'
from pathlib import Path
import hashlib,json,re,shutil,sys
root=Path(sys.argv[1]);base_commit=sys.argv[2]
shutil.copytree(root/'a5',root/'a51');shutil.copytree(root/'engine_a5',root/'engine_a51');(root/'tests_a51').mkdir()
shutil.copy2(root/'MANIFEST_SHA256.json',root/'BASELINE_A5_MANIFEST_SHA256.json')
for p in sorted((root/'a51').rglob('*')):
    if p.is_file() and p.suffix in ('.py','.html'):
        s=p.read_text(encoding='utf-8').replace('a5.','a51.').replace("'a5.supervisor'","'a51.supervisor'");p.write_text(s,encoding='utf-8')
p=root/'a51/common.py';s=p.read_text();s=s.replace('MINUTES=90','MINUTES=540').replace('DURATION_MS=5_400_000','DURATION_MS=32_400_000').replace("VERSION='BELIAL_A5_16Q_V1'","VERSION='BELIAL_A51_16Q_9H_V1'");p.write_text(s)
(root/'a51/cache.py').write_text('"""mtime/size keyed JSON cache for hot-path status files."""\nfrom __future__ import annotations\nfrom pathlib import Path\nfrom a51.common import read_json\n\nclass JsonCache:\n    def __init__(self):\n        self._items={};self.hits=0;self.misses=0;self.missing=0\n    def get(self,path,default=None):\n        path=Path(path)\n        try:stat=path.stat()\n        except OSError:self.missing+=1;return default\n        sig=(stat.st_mtime_ns,stat.st_size)\n        cached=self._items.get(path)\n        if cached is not None and cached[0]==sig:\n            self.hits+=1;return cached[1]\n        value=read_json(path,default);self._items[path]=(sig,value);self.misses+=1;return value\n    def view(self):\n        total=self.hits+self.misses\n        return {\'hits\':self.hits,\'misses\':self.misses,\'missing\':self.missing,\'entries\':len(self._items),\'hit_ratio\':self.hits/total if total else None}\n');(root/'a51/terminal_projection.py').write_text('"""Bounded display projections; never used by the PAPER decision path."""\nfrom __future__ import annotations\nimport math\nCURVE_MAX_POINTS=240\nCLOSED_POSITION_HISTORY=60\nRECENT_ROWS=20\n\ndef envelope(points,maximum=CURVE_MAX_POINTS):\n    if not isinstance(points,list):return []\n    if len(points)<=maximum:return points\n    bucket=max(1,math.ceil((len(points)-2)/max(1,(maximum-2)//2)));out=[points[0]]\n    for i in range(1,len(points)-1,bucket):\n        rows=points[i:min(i+bucket,len(points)-1)]\n        valid=[r for r in rows if isinstance(r,list) and len(r)>=2 and isinstance(r[1],(float,int))]\n        if len(valid)!=len(rows):\n            if rows:out.append([rows[0][0],None])\n            if valid:out.append(valid[-1])\n            continue\n        lo=min(valid,key=lambda x:x[1]);hi=max(valid,key=lambda x:x[1]);out.extend(sorted([lo,hi] if lo!=hi else [lo],key=lambda x:x[0]))\n    out.append(points[-1]);return out\n\ndef group_view(group):\n    keep=(\'at_ms\',\'cohort\',\'complete\',\'counts\',\'orders_pending\',\'scenarios\',\'gate\',\'not_strategy_pnl\',\'paper_only\',\'orders\',\'signal_engine_feedback\')\n    out={k:group[k] for k in keep if k in group};pos=group.get(\'positions\',[])\n    active=[r for r in pos if r.get(\'closed_ms\') is None];closed=[r for r in pos if r.get(\'closed_ms\') is not None]\n    out[\'positions\']=active+closed[-CLOSED_POSITION_HISTORY:];out[\'recent\']=group.get(\'recent\',[])[-RECENT_ROWS:]\n    out[\'curves\']={k:envelope(v) for k,v in group.get(\'curves\',{}).items()}\n    out[\'display\']={\'curves_downsampled_envelope\':True,\'curve_max_points\':CURVE_MAX_POINTS,\'closed_history_limit\':CLOSED_POSITION_HISTORY,\'source_position_rows\':len(pos),\'shown_position_rows\':len(out[\'positions\']),\'not_an_equity_calculator\':True}\n    return out\n\ndef compact_state(state):\n    drop=(\'curves\',\'positions\',\'recent\',\'forward_descriptive\',\'groups\',\'a1_cohort\',\'main\',\'shadow\',\'shadow_focus\')\n    out={k:v for k,v in state.items() if k not in drop};groups=state.get(\'groups\') or {\'CORE12\':state}\n    out[\'groups\']={g:group_view(v) for g,v in groups.items()}\n    for name in (\'main\',\'shadow\',\'shadow_focus\'):\n        value=state.get(name,{}) or {};out[name]={k:value[k] for k in (\'counts\',\'policies\',\'schema\',\'paper_only\',\'phase\',\'state\',\'status\') if k in value}\n    out[\'terminal_projection\']=\'A51_BOUNDED_DISPLAY_NOT_LEDGER\';return out\n\ndef compact_agent(agent):\n    out={k:v for k,v in agent.items() if k!=\'models\'};heads={}\n    for r in agent.get(\'models\',[]):\n        k=r[\'head_id\'];h=heads.setdefault(k,dict(primary_n=0,brier_sum=0.,baseline_sum=0.));n=r.get(\'primary_eval_n\',0)\n        if n:h[\'primary_n\']+=n;h[\'brier_sum\']+=n*r[\'primary_brier\'];h[\'baseline_sum\']+=n*r[\'baseline_primary_brier\']\n    out[\'head_scores\']={k:{\'n\':v[\'primary_n\'],\'brier\':v[\'brier_sum\']/v[\'primary_n\'] if v[\'primary_n\'] else None,\'baseline_brier\':v[\'baseline_sum\']/v[\'primary_n\'] if v[\'primary_n\'] else None} for k,v in heads.items()};return out\n\ndef compact_books(books):\n    keep=(\'at_ms\',\'books\',\'transport\',\'venue_runtime\',\'book_state_counts\',\'ingest_messages\',\'messages_per_second\',\'ingest_queue\',\'rust_to_paper_ms_p50\',\'rust_to_paper_ms_p95\')\n    out={k:books[k] for k in keep if k in books};out[\'book_errors\']=(books.get(\'book_errors\') or [])[-4:];return out\n\ndef compact_platform(platform):\n    out={k:platform[k] for k in (\'schema\',\'at_ms\',\'phase\',\'stage\',\'experiment_label\',\'raw_counts\',\'integrity\',\'paper_stale\',\'failures\',\'orders\',\'coverage_peak\',\'cache\') if k in platform}\n    cov=platform.get(\'coverage\') or {};rows=[]\n    for r in cov.get(\'rows\',[]):\n        ss=r.get(\'source_status\') or {};rows.append({k:r.get(k) for k in (\'symbol\',\'cohort\',\'identity\',\'raw_capture\',\'price\',\'actor\',\'qualified\',\'reason\',\'state\',\'pool\',\'protocol\',\'signal_source_ready\',\'signal_source_reason\',\'venues_ready\',\'venue_states\',\'paper_prerequisites_ready\')}|{\'source_status\':{k:ss.get(k) for k in (\'state\',\'at_ms\',\'reason\',\'actor_ready\',\'generation\',\'integrity\')}})\n    out[\'coverage\']={k:cov.get(k) for k in (\'requested\',\'qualified_at_start\',\'live_signal_sources\',\'usable_books\',\'paper_prerequisites_ready\',\'not_a_signal_or_profit_guarantee\')};out[\'coverage\'][\'rows\']=rows\n    comps={}\n    for name,v in (platform.get(\'components\') or {}).items():\n        item={k:v.get(k) for k in (\'state\',\'status\',\'at_ms\',\'process_alive\',\'returncode\',\'heartbeat_age_ms\',\'error\',\'reason\',\'component\',\'command\',\'elapsed_seconds\',\'native_pid\') if k in v}\n        if v.get(\'last_log\'):item[\'last_log\']=str(v[\'last_log\'])[-1800:]\n        comps[name]=item\n    out[\'components\']=comps;return out\n\ndef compact_satellites(value):\n    if not isinstance(value,dict):return {}\n    out={k:value.get(k) for k in (\'schema\',\'at_ms\',\'state\',\'shadow_only\',\'orders\',\'affects_trading\',\'poll_seconds\',\'venues\')}\n    out[\'by_symbol\']={s:{k:r.get(k) for k in (\'state\',\'direction\',\'agreement_count\',\'fresh_count\',\'price_divergence_bps\',\'venues\')} for s,r in (value.get(\'by_symbol\') or {}).items()};return out\n');(root/'a51/ui_worker.py').write_text('"""Read-only local terminal server with distinct-payload SSE coalescing."""\nfrom __future__ import annotations\nimport argparse,socket,threading\nfrom http.server import BaseHTTPRequestHandler,ThreadingHTTPServer\nfrom a51.common import *\nfrom a51.terminal_projection import compact_agent,compact_books,compact_platform,compact_satellites,compact_state\n\nclass Bus:\n    def __init__(self):\n        self.cv=threading.Condition();self.payloads={};self.last={};self.seq=0;self.clients=0;self.closed=False;self.max_payload=0;self.published=0;self.coalesced=0\n    def publish(self,kind,value):\n        data=canonical(value);encoded=data.encode()\n        if len(encoded)>2<<20:raise RuntimeError(\'UI_PROJECTION_TOO_LARGE\')\n        if self.last.get(kind)==data:self.coalesced+=1;return False\n        with self.cv:\n            self.seq+=1;self.payloads[kind]=(self.seq,data);self.last[kind]=data;self.published+=1;self.max_payload=max(self.max_payload,len(encoded));self.cv.notify_all()\n        return True\n    def snapshot(self):\n        with self.cv:return {k:strict_loads(v[1]) for k,v in self.payloads.items()}\n\ndef make_server(bus,html):\n    content=Path(html).read_bytes()\n    class Handler(BaseHTTPRequestHandler):\n        protocol_version=\'HTTP/1.1\'\n        def log_message(self,*args):pass\n        def response(self,data,ctype=\'application/json\',status=200):\n            self.send_response(status);self.send_header(\'Content-Type\',ctype);self.send_header(\'Content-Length\',str(len(data)));self.send_header(\'Cache-Control\',\'no-store\');self.send_header(\'X-Content-Type-Options\',\'nosniff\');self.send_header(\'Content-Security-Policy\',"default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; connect-src \'self\'; img-src data:; font-src \'none\'; frame-ancestors \'none\'; base-uri \'none\'");self.end_headers();self.wfile.write(data)\n        def do_GET(self):\n            if self.headers.get(\'Host\',\'\').split(\':\')[0] not in (\'localhost\',\'127.0.0.1\'):self.send_error(403);return\n            if self.path==\'/\':self.response(content,\'text/html; charset=utf-8\');return\n            if self.path==\'/health\':self.response(canonical({\'ok\':True,\'read_only\':True,\'orders\':False,\'seq\':bus.seq,\'clients\':bus.clients}).encode());return\n            if self.path==\'/api/snapshot\':self.response(canonical(bus.snapshot()).encode());return\n            if self.path!=\'/events\':self.send_error(404);return\n            origin=self.headers.get(\'Origin\');allowed=(\'http://127.0.0.1:\'+str(self.server.server_port),\'http://localhost:\'+str(self.server.server_port))\n            if origin and origin not in allowed:self.send_error(403);return\n            with bus.cv:\n                if bus.clients>=4:self.send_error(429);return\n                bus.clients+=1\n            try:\n                self.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1);self.connection.settimeout(1.0);self.send_response(200);self.send_header(\'Content-Type\',\'text/event-stream\');self.send_header(\'Cache-Control\',\'no-store\');self.send_header(\'X-Accel-Buffering\',\'no\');self.end_headers();last={}\n                while not bus.closed:\n                    with bus.cv:\n                        updates=sorted([(kind,seq,data) for kind,(seq,data) in bus.payloads.items() if seq!=last.get(kind)],key=lambda x:x[1])\n                        if not updates:bus.cv.wait(.1);continue\n                    for kind,seq,data in updates:self.wfile.write(f\'event: {kind}\\nid: {seq}\\ndata: {data}\\n\\n\'.encode());last[kind]=seq\n                    self.wfile.flush()\n            except (OSError,ConnectionError):pass\n            finally:\n                with bus.cv:bus.clients-=1\n    server=ThreadingHTTPServer((\'127.0.0.1\',0),Handler);server.daemon_threads=True;threading.Thread(target=server.serve_forever,daemon=True).start();return server\n\ndef project(kind,value):\n    if kind==\'state\':return compact_state(value)\n    if kind==\'books\':return compact_books(value)\n    if kind==\'platform\':return compact_platform(value)\n    if kind==\'agent\':return compact_agent(value)\n    if kind==\'satellites\':return compact_satellites(value)\n    return value\n\ndef run(session):\n    session=Path(session);out=session/\'ui\';out.mkdir(exist_ok=True);stop=Stop().install();bus=Bus();server=make_server(bus,ROOT/\'a51/web/dashboard.html\');url=f\'http://127.0.0.1:{server.server_port}/\';atomic(out/\'URL.json\',{\'url\':url,\'pid\':os.getpid(),\'read_only\':True});print(\'DASHBOARD_URL=\'+url,flush=True)\n    paths={\'state\':session/\'paper/state.json\',\'books\':session/\'paper/books.json\',\'platform\':session/\'PLATFORM.json\',\'agent\':session/\'n2/AGENT_VIEW.json\',\'agent_status\':session/\'n2/status.json\',\'sampler\':session/\'price_samples/status.json\',\'ws_relay\':session/\'ws_relay/status.json\',\'satellites\':session/\'satellites/state.json\'}\n    signatures={};last_metrics=0;previous=0\n    try:\n        while not stop.value:\n            for kind,path in paths.items():\n                try:mtime=path.stat().st_mtime_ns\n                except FileNotFoundError:continue\n                if signatures.get(kind)==mtime:continue\n                value=read_json(path)\n                if value is None:continue\n                bus.publish(kind,project(kind,value));signatures[kind]=mtime\n            at=now_ms()\n            if at-last_metrics>=1000:\n                metrics={\'at_ms\':at,\'display_updates_per_second\':bus.published-previous,\'maximum_payload_bytes\':bus.max_payload,\'coalesced_payloads\':bus.coalesced,\'clients\':bus.clients,\'coalescing\':\'LATEST_DISTINCT_VIEW_ONLY_LEDGER_UNTOUCHED\',\'push_target_hz\':20,\'orders\':False};bus.publish(\'ui\',metrics);heartbeat(out/\'status.json\',\'SERVING\',**{k:v for k,v in metrics.items() if k!=\'at_ms\'});previous=bus.published;last_metrics=at\n            if (session/\'UI_STOP\').exists():break\n            time.sleep(.05)\n    except Exception as exc:heartbeat(out/\'status.json\',\'FAILED\',error=str(exc));raise\n    finally:\n        bus.closed=True\n        with bus.cv:bus.cv.notify_all()\n        server.shutdown();server.server_close()\n    return 0\nif __name__==\'__main__\':\n    ap=argparse.ArgumentParser();ap.add_argument(\'--session\',type=Path,required=True);a=ap.parse_args();raise SystemExit(run(a.session))\n');(root/'a51/satellite_worker.py').write_text('"""Read-only external satellites. Never imported by PAPER/strategy modules."""\nfrom __future__ import annotations\n\nimport argparse\nimport collections\nimport concurrent.futures\nimport json\nimport math\nimport re\nimport time\nimport urllib.request\nfrom pathlib import Path\n\nfrom a51.common import SYMBOLS, Stop, atomic, heartbeat, now_ms, session_expired\nfrom a51.journal import Journal\n\nPOLL_SECONDS = 3.0\nMAX_RESPONSE_BYTES = 24 << 20\nUSER_AGENT = "Belial-A51-SATELLITE-READ-ONLY/1"\nFRESH_MS = 10_000\nDIRECTION_THRESHOLD_BPS = 2.0\nJOURNAL_EVERY_MS = 30_000\n\nBINANCE_BOOK = "https://fapi.binance.com/fapi/v1/ticker/bookTicker"\nBINANCE_PREMIUM = "https://fapi.binance.com/fapi/v1/premiumIndex"\nBYBIT_TICKERS = "https://api.bybit.com/v5/market/tickers?category=linear"\nKRAKEN_TICKERS = "https://futures.kraken.com/derivatives/api/v3/tickers"\nVENUES = ("binance", "bybit", "kraken")\n\n\ndef number(value):\n    if isinstance(value, (list, tuple)):\n        value = value[0] if value else None\n    if value in (None, "") or isinstance(value, bool):\n        return None\n    try:\n        result = float(value)\n    except (TypeError, ValueError):\n        return None\n    return result if math.isfinite(result) else None\n\n\ndef positive(value):\n    value = number(value)\n    return value if value is not None and value > 0 else None\n\n\ndef resolve_market(name):\n    if not isinstance(name, str) or not name:\n        return None\n    clean = re.sub(r"[^A-Z0-9]", "", name.upper())\n    for prefix in ("PERP", "PF", "PI"):\n        if clean.startswith(prefix):\n            clean = clean[len(prefix):]\n    for suffix in ("PERPETUAL", "PERP", "USDT", "USD"):\n        if clean.endswith(suffix):\n            clean = clean[:-len(suffix)]\n            break\n    for symbol in sorted(SYMBOLS, key=len, reverse=True):\n        if clean == symbol:\n            return symbol, 1\n        if clean in ("1000" + symbol, "K" + symbol):\n            return symbol, 1000\n        if clean == "1000000" + symbol:\n            return symbol, 1_000_000\n    return None\n\n\ndef normalized_row(venue, market, bid, ask, at_ms, **extra):\n    resolved = resolve_market(market)\n    if not resolved:\n        return None\n    symbol, multiplier = resolved\n    bid = positive(bid)\n    ask = positive(ask)\n    if bid is None or ask is None or bid >= ask:\n        return None\n    bid /= multiplier\n    ask /= multiplier\n    mid = (bid + ask) / 2\n    row = {\n        "venue": venue,\n        "symbol": symbol,\n        "market": market,\n        "multiplier": multiplier,\n        "bid": bid,\n        "ask": ask,\n        "mid": mid,\n        "spread_bps": (ask - bid) / mid * 10000,\n        "at_ms": int(at_ms),\n        "orders": False,\n        "shadow_only": True,\n    }\n    row.update(extra)\n    return row\n\n\ndef choose(rows):\n    chosen = {}\n    for row in rows:\n        if not row:\n            continue\n        current = chosen.get(row["symbol"])\n        score = (\n            0 if row["multiplier"] == 1 else 1,\n            row.get("spread_bps", float("inf")),\n        )\n        old_score = (\n            0 if current and current["multiplier"] == 1 else 1,\n            current.get("spread_bps", float("inf")) if current else float("inf"),\n        )\n        if current is None or score < old_score:\n            chosen[row["symbol"]] = row\n    return chosen\n\n\ndef fetch_json(url):\n    request = urllib.request.Request(\n        url,\n        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},\n        method="GET",\n    )\n    with urllib.request.urlopen(request, timeout=5) as response:\n        raw = response.read(MAX_RESPONSE_BYTES + 1)\n    if len(raw) > MAX_RESPONSE_BYTES:\n        raise ValueError("SATELLITE_RESPONSE_TOO_LARGE")\n    return json.loads(raw)\n\n\ndef binance_snapshot(at_ms):\n    books = fetch_json(BINANCE_BOOK)\n    premium = fetch_json(BINANCE_PREMIUM)\n    books = [books] if isinstance(books, dict) else books\n    premium = [premium] if isinstance(premium, dict) else premium\n    if not isinstance(books, list) or not isinstance(premium, list):\n        raise ValueError("BINANCE_SCHEMA")\n    premium_map = {\n        row.get("symbol"): row\n        for row in premium\n        if isinstance(row, dict) and row.get("symbol")\n    }\n    rows = []\n    for row in books:\n        if not isinstance(row, dict):\n            continue\n        market = row.get("symbol")\n        meta = premium_map.get(market, {})\n        multiplier = (resolve_market(market) or (None, 1))[1]\n        mark = positive(meta.get("markPrice"))\n        index = positive(meta.get("indexPrice"))\n        rows.append(normalized_row(\n            "binance",\n            market,\n            row.get("bidPrice"),\n            row.get("askPrice"),\n            at_ms,\n            mark_price=mark / multiplier if mark else None,\n            index_price=index / multiplier if index else None,\n            funding_rate=number(meta.get("lastFundingRate")),\n            open_interest=None,\n            source_time_ms=int(number(row.get("time")) or at_ms),\n        ))\n    return choose(rows)\n\n\ndef bybit_snapshot(at_ms):\n    document = fetch_json(BYBIT_TICKERS)\n    rows = (((document or {}).get("result") or {}).get("list") or [])\n    if not isinstance(rows, list):\n        raise ValueError("BYBIT_SCHEMA")\n    parsed = []\n    for row in rows:\n        if not isinstance(row, dict):\n            continue\n        market = row.get("symbol")\n        multiplier = (resolve_market(market) or (None, 1))[1]\n        mark = positive(row.get("markPrice"))\n        index = positive(row.get("indexPrice"))\n        parsed.append(normalized_row(\n            "bybit",\n            market,\n            row.get("bid1Price"),\n            row.get("ask1Price"),\n            at_ms,\n            mark_price=mark / multiplier if mark else None,\n            index_price=index / multiplier if index else None,\n            funding_rate=number(row.get("fundingRate")),\n            open_interest=number(row.get("openInterest")),\n            source_time_ms=int(number((document or {}).get("time")) or at_ms),\n        ))\n    return choose(parsed)\n\n\ndef kraken_snapshot(at_ms):\n    document = fetch_json(KRAKEN_TICKERS)\n    rows = (document or {}).get("tickers") or []\n    if not isinstance(rows, list):\n        raise ValueError("KRAKEN_SCHEMA")\n    parsed = []\n    for row in rows:\n        if not isinstance(row, dict):\n            continue\n        market = row.get("symbol") or row.get("pair")\n        multiplier = (resolve_market(market) or (None, 1))[1]\n        mark = positive(row.get("markPrice"))\n        index = positive(row.get("index"))\n        parsed.append(normalized_row(\n            "kraken",\n            market,\n            row.get("bid"),\n            row.get("ask"),\n            at_ms,\n            mark_price=mark / multiplier if mark else None,\n            index_price=index / multiplier if index else None,\n            funding_rate=number(\n                row.get("fundingRate")\n                if row.get("fundingRate") is not None\n                else row.get("fundingRatePrediction")\n            ),\n            open_interest=number(row.get("openInterest")),\n            source_time_ms=at_ms,\n        ))\n    return choose(parsed)\n\n\nFETCHERS = {\n    "binance": binance_snapshot,\n    "bybit": bybit_snapshot,\n    "kraken": kraken_snapshot,\n}\n\n\ndef history_return(history, venue, symbol, at_ms, mid):\n    values = history[(venue, symbol)]\n    values.append((at_ms, mid))\n    while values and at_ms - values[0][0] > 30_000:\n        values.popleft()\n    reference = None\n    for ts, price in reversed(values):\n        if ts <= at_ms - 8_000:\n            reference = price\n            break\n    if reference is None or reference <= 0:\n        return None\n    return (mid / reference - 1) * 10000\n\n\ndef direction(return_bps):\n    if return_bps is None:\n        return "UNKNOWN"\n    if return_bps >= DIRECTION_THRESHOLD_BPS:\n        return "UP"\n    if return_bps <= -DIRECTION_THRESHOLD_BPS:\n        return "DOWN"\n    return "FLAT"\n\n\ndef aggregate_symbol(symbol, venue_rows, at_ms):\n    rows = {}\n    directions = []\n    mids = []\n    for venue in VENUES:\n        row = venue_rows.get(venue, {}).get(symbol)\n        if not row:\n            rows[venue] = {\n                "venue": venue,\n                "supported": False,\n                "fresh": False,\n                "direction": "UNKNOWN",\n                "orders": False,\n                "shadow_only": True,\n            }\n            continue\n        item = dict(row)\n        item["age_ms"] = max(0, at_ms - int(item.get("at_ms", at_ms)))\n        item["fresh"] = item["age_ms"] <= FRESH_MS\n        item["supported"] = True\n        rows[venue] = item\n        if item["fresh"]:\n            mids.append(item["mid"])\n            if item.get("direction") in ("UP", "DOWN"):\n                directions.append(item["direction"])\n\n    up = directions.count("UP")\n    down = directions.count("DOWN")\n    if max(up, down) >= 2:\n        state = "CONFIRMED"\n        consensus = "UP" if up > down else "DOWN"\n        agreement = max(up, down)\n    elif up and down:\n        state = "DIVERGENT"\n        consensus = "MIXED"\n        agreement = max(up, down)\n    else:\n        state = "NEUTRAL"\n        consensus = directions[0] if directions else "FLAT"\n        agreement = max(up, down)\n\n    divergence = None\n    if len(mids) >= 2:\n        center = sum(mids) / len(mids)\n        if center > 0:\n            divergence = (max(mids) - min(mids)) / center * 10000\n\n    return {\n        "symbol": symbol,\n        "state": state,\n        "direction": consensus,\n        "agreement_count": agreement,\n        "fresh_count": sum(bool(row.get("fresh")) for row in rows.values()),\n        "price_divergence_bps": divergence,\n        "venues": rows,\n        "funding_comparison": "RAW_RATES_NOT_NORMALIZED_ACROSS_INTERVALS",\n        "open_interest_comparison": "RAW_VENUE_UNITS_NOT_AGGREGATED",\n        "orders": False,\n        "shadow_only": True,\n        "affects_trading": False,\n    }\n\n\ndef collect(history, last_good, executor):\n    at_ms = now_ms()\n    results = {}\n    venue_status = {}\n    futures = {\n        venue: executor.submit(fetcher, at_ms)\n        for venue, fetcher in FETCHERS.items()\n    }\n    for venue, future in futures.items():\n        try:\n            result = future.result(timeout=12)\n            last_good[venue] = result\n            results[venue] = result\n            venue_status[venue] = {\n                "state": "ACTIVE",\n                "symbols": len(result),\n                "error": None,\n                "last_success_ms": at_ms,\n            }\n        except Exception as exc:\n            previous = last_good.get(venue, {})\n            results[venue] = previous\n            last_success = max(\n                [int(row.get("at_ms", 0)) for row in previous.values()] or [0]\n            )\n            venue_status[venue] = {\n                "state": "DEGRADED",\n                "symbols": len(previous),\n                "error": str(exc)[:500],\n                "last_success_ms": last_success or None,\n            }\n\n    for venue, rows in results.items():\n        for symbol, row in rows.items():\n            return_bps = history_return(history, venue, symbol, at_ms, row["mid"])\n            row["return_10s_bps"] = return_bps\n            row["direction"] = direction(return_bps)\n\n    by_symbol = {\n        symbol: aggregate_symbol(symbol, results, at_ms)\n        for symbol in SYMBOLS\n    }\n    active = sum(row["state"] == "ACTIVE" for row in venue_status.values())\n    state = "ACTIVE" if active == len(VENUES) else "DEGRADED" if active else "UNAVAILABLE"\n    return {\n        "schema": "belial_a51_satellites_v1",\n        "at_ms": at_ms,\n        "state": state,\n        "venues": venue_status,\n        "by_symbol": by_symbol,\n        "poll_seconds": POLL_SECONDS,\n        "shadow_only": True,\n        "orders": False,\n        "affects_trading": False,\n        "not_a_trade_permission": True,\n    }\n\n\ndef self_test():\n    assert resolve_market("WIFUSDT") == ("WIF", 1)\n    assert resolve_market("1000BONKUSDT") == ("BONK", 1000)\n    assert resolve_market("PF_WIFUSD") == ("WIF", 1)\n    assert resolve_market("DRIFTUSDT") is None\n    assert direction(3) == "UP"\n    assert direction(-3) == "DOWN"\n    assert direction(0.2) == "FLAT"\n    row = normalized_row("x", "WIFUSDT", "1", "1.01", 10)\n    assert row and row["symbol"] == "WIF"\n    return True\n\n\ndef run(session, once=False):\n    session = Path(session)\n    out = session / "satellites"\n    out.mkdir(parents=True, exist_ok=True)\n    stop = Stop().install()\n    history = collections.defaultdict(collections.deque)\n    last_good = {}\n    journal = Journal(out / "events.jsonl")\n    last_journal = 0\n\n    try:\n        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:\n            while not stop.value and not session_expired(session):\n                started = time.monotonic()\n                state = collect(history, last_good, executor)\n                atomic(out / "state.json", state)\n                heartbeat(\n                    out / "status.json",\n                    state["state"],\n                    venues=state["venues"],\n                    shadow_only=True,\n                    orders=False,\n                    affects_trading=False,\n                )\n                if state["at_ms"] - last_journal >= JOURNAL_EVERY_MS:\n                    journal.append({\n                        "kind": "SATELLITE_SNAPSHOT",\n                        "body": state,\n                        "orders": False,\n                        "shadow_only": True,\n                    })\n                    last_journal = state["at_ms"]\n                if once:\n                    break\n                time.sleep(max(0.05, POLL_SECONDS - (time.monotonic() - started)))\n    finally:\n        journal.close()\n        heartbeat(\n            out / "status.json",\n            "STOPPED",\n            shadow_only=True,\n            orders=False,\n            affects_trading=False,\n        )\n    return 0\n\n\nif __name__ == "__main__":\n    parser = argparse.ArgumentParser()\n    parser.add_argument("--session", type=Path)\n    parser.add_argument("--once", action="store_true")\n    parser.add_argument("--self-test", action="store_true")\n    args = parser.parse_args()\n    if args.self_test:\n        self_test()\n        print("A51_SATELLITE_SELF_TEST=PASS")\n        raise SystemExit(0)\n    if args.session is None:\n        raise SystemExit("--session required")\n    raise SystemExit(run(args.session, args.once))\n');(root/'a51/verify.py').write_text('"""Offline verification for isolated A5.1 16Q 9-hour package."""\nfrom __future__ import annotations\nimport argparse,ast,json,os,re,subprocess,sys\nfrom pathlib import Path\nfrom a51.common import ROOT,digest,safe\nMANIFEST=\'MANIFEST_SHA256.json\'\n\ndef verify_files(root=ROOT):\n    root=safe(root);manifest=json.loads((root/MANIFEST).read_text());files=manifest.get(\'files\')\n    if manifest.get(\'schema\')!=\'belial_a51_16q_9h_package_v1\' or not isinstance(files,dict) or not files:raise RuntimeError(\'INVALID_A51_PACKAGE_MANIFEST\')\n    actual={str(p.relative_to(root)) for p in root.rglob(\'*\') if p.is_file() and str(p.relative_to(root))!=MANIFEST}\n    for p in root.rglob(\'*\'):safe(p)\n    if actual!=set(files):raise RuntimeError(\'A51_PACKAGE_MEMBERS_CHANGED:\'+str(sorted(actual.symmetric_difference(files))[:30]))\n    for rel,expected in files.items():\n        p=Path(rel)\n        if p.is_absolute() or \'..\' in p.parts or not re.fullmatch(\'[0-9a-f]{64}\',str(expected)):raise RuntimeError(\'INVALID_A51_MANIFEST_PATH_OR_DIGEST\')\n        if digest(root/p)!=expected:raise RuntimeError(\'A51_PACKAGE_HASH_MISMATCH:\'+rel)\n    return len(files)\n\ndef test_suite(root=ROOT,output=None):\n    root=Path(root);env=dict(os.environ,PYTHONDONTWRITEBYTECODE=\'1\',PYTHONPATH=str(root),BELIAL_A1_NO_BROWSER=\'1\');argv=[sys.executable,\'-B\',\'-m\',\'unittest\',\'discover\',\'-s\',str(root/\'tests_a51\'),\'-v\'];p=subprocess.run(argv,cwd=root,env=env,capture_output=True,text=True,timeout=300);log=p.stdout+p.stderr;match=re.search(r\'Ran (\\d+) tests?\',log);n=int(match.group(1)) if match else 0\n    if output:output=Path(output);output.mkdir(parents=True,exist_ok=True);(output/\'a51_tests.log\').write_text(log)\n    passed=p.returncode==0 and n>0;print(\'A51_TESTS=\'+str(n)+\'; PASS=\'+str(passed).lower(),flush=True)\n    if not passed:print(log[-30000:],file=sys.stderr);raise RuntimeError(\'A51_OFFLINE_SUITE_FAILED\')\n    return n\n\ndef main():\n    ap=argparse.ArgumentParser();ap.add_argument(\'--hashes-only\',action=\'store_true\');ap.add_argument(\'--log-dir\',type=Path);a=ap.parse_args()\n    if a.log_dir:\n        resolved=a.log_dir.resolve()\n        if resolved==ROOT or ROOT in resolved.parents:raise SystemExit(\'Logs must stay outside the sealed release.\')\n    n=verify_files();print(\'A51_PACKAGE_HASHES=PASS; FILES=\'+str(n),flush=True)\n    if not a.hashes_only:\n        for f in ROOT.rglob(\'*.py\'):ast.parse(f.read_text(),filename=str(f))\n        tests=test_suite(output=a.log_dir);result={\'ok\':True,\'tests\':tests,\'orders\':False,\'external_market_test\':False,\'native_rust_executed\':False}\n        if a.log_dir:(a.log_dir/\'VERIFY_RESULT.json\').write_text(json.dumps(result,indent=2)+\'\\n\')\n        print(\'A51_OFFLINE_VERIFIED=\'+str(tests)+\'; RUST=NOT_EXECUTED; ORDERS=false\',flush=True)\n    return 0\nif __name__==\'__main__\':raise SystemExit(main())\n');(root/'tests_a51/test_contract.py').write_text('import ast\nimport http.client\nimport json\nimport tempfile\nimport unittest\nfrom pathlib import Path\n\nfrom a51.cache import JsonCache\nfrom a51.common import ROOT, SYMBOLS, CORE12, FOCUS4, MINUTES, DURATION_MS, VERSION\nfrom a51.satellite_worker import self_test, aggregate_symbol\nfrom a51.ui_worker import Bus, make_server\n\n\nclass A51ContractTests(unittest.TestCase):\n    def test_exact_16q_universe_preserved(self):\n        self.assertEqual(\n            (len(SYMBOLS), len(CORE12), FOCUS4),\n            (16, 12, ("RENDER", "KMNO", "MEW", "MPLX")),\n        )\n\n    def test_nine_hour_duration(self):\n        self.assertEqual(\n            (MINUTES, DURATION_MS, VERSION),\n            (540, 32_400_000, "BELIAL_A51_16Q_9H_V1"),\n        )\n\n    def test_satellite_self_test(self):\n        self.assertTrue(self_test())\n\n    def test_satellites_are_not_in_paper_decision_modules(self):\n        for relative in (\n            "a51/paper_controller.py",\n            "a51/analysis_worker.py",\n            "a51/economics.py",\n        ):\n            source = (ROOT / relative).read_text()\n            self.assertNotIn("satellite_worker", source)\n            self.assertNotIn("satellites/state.json", source)\n\n    def test_satellite_worker_is_read_only_shadow(self):\n        source = (ROOT / "a51/satellite_worker.py").read_text()\n        tree = ast.parse(source)\n\n        # Runtime semantic contract; independent of formatter/quote style.\n        probe = aggregate_symbol("WIF", {}, 0)\n        self.assertIs(probe["affects_trading"], False)\n        self.assertIs(probe["orders"], False)\n        self.assertIs(probe["shadow_only"], True)\n\n        # Every explicit urllib Request method must be GET.\n        methods = []\n        for node in ast.walk(tree):\n            if not isinstance(node, ast.Call):\n                continue\n            func = node.func\n            if not (isinstance(func, ast.Attribute) and func.attr == "Request"):\n                continue\n            for keyword in node.keywords:\n                if (\n                    keyword.arg == "method"\n                    and isinstance(keyword.value, ast.Constant)\n                    and isinstance(keyword.value.value, str)\n                ):\n                    methods.append(keyword.value.value.upper())\n\n        self.assertTrue(methods)\n        self.assertEqual(set(methods), {"GET"})\n        self.assertNotIn("order.place", source)\n        self.assertNotIn("apiKey", source)\n\n    def test_ui_bus_coalesces_identical_payload(self):\n        bus = Bus()\n        self.assertTrue(bus.publish("x", {"a": 1}))\n        self.assertFalse(bus.publish("x", {"a": 1}))\n        self.assertEqual(bus.coalesced, 1)\n        self.assertEqual(bus.published, 1)\n\n    def test_ui_health_is_read_only(self):\n        bus = Bus()\n        server = make_server(bus, ROOT / "a51/web/dashboard.html")\n        try:\n            client = http.client.HTTPConnection(\n                "127.0.0.1",\n                server.server_port,\n                timeout=2,\n            )\n            client.request("GET", "/health")\n            response = client.getresponse()\n            payload = json.loads(response.read())\n            client.close()\n\n            self.assertEqual(response.status, 200)\n            self.assertTrue(payload["read_only"])\n            self.assertFalse(payload["orders"])\n        finally:\n            bus.closed = True\n            with bus.cv:\n                bus.cv.notify_all()\n            server.shutdown()\n            server.server_close()\n\n    def test_json_cache_reuses_unchanged_file(self):\n        with tempfile.TemporaryDirectory() as td:\n            path = Path(td) / "x.json"\n            path.write_text(\'{"x":1}\\n\')\n            cache = JsonCache()\n            self.assertEqual(cache.get(path), {"x": 1})\n            self.assertEqual(cache.get(path), {"x": 1})\n            self.assertEqual(\n                (cache.view()["misses"], cache.view()["hits"]),\n                (1, 1),\n            )\n\n    def test_paper_worker_hoists_repeated_reads(self):\n        source = (ROOT / "a51/paper_worker.py").read_text()\n        self.assertIn("analysis_blocked=cache.get", source)\n        self.assertIn("analysis_health=cache.get", source)\n        self.assertIn("heads=heads", source)\n        self.assertNotIn(\n            "analysis_bad=read_json(session/\'ANALYSIS_BLOCKED.json\',{}).get(s)",\n            source,\n        )\n\n    def test_supervisor_cache_peak_and_satellite(self):\n        source = (ROOT / "a51/supervisor.py").read_text()\n        self.assertIn("self.cache=JsonCache()", source)\n        self.assertIn("self.coverage_peak", source)\n        self.assertIn(\n            "self.launch(\'satellites\',\'satellite_worker\')",\n            source,\n        )\n\n    def test_ui_isolated_dashboard_and_satellites(self):\n        source = (ROOT / "a51/ui_worker.py").read_text().replace(" ", "")\n        self.assertIn("ROOT/\'a51/web/dashboard.html\'", source)\n        self.assertIn(\n            "\'satellites\':session/\'satellites/state.json\'",\n            source,\n        )\n        self.assertIn("coalesced_payloads", source)\n\n    def test_projection_limits_display_only(self):\n        source = (ROOT / "a51/terminal_projection.py").read_text().replace(" ", "")\n        self.assertIn("CURVE_MAX_POINTS=240", source)\n        self.assertIn("CLOSED_POSITION_HISTORY=60", source)\n        self.assertIn("A51_BOUNDED_DISPLAY_NOT_LEDGER", source)\n\n    def test_dashboard_satellite_panel(self):\n        html = (ROOT / "a51/web/dashboard.html").read_text()\n        self.assertIn(\'id="satelliteRows"\', html)\n        self.assertIn(\'id="satRibbon"\', html)\n        self.assertIn("Binance / Bybit / Kraken", html)\n\n    def test_engine_member_set_matches_a5(self):\n        left_root = ROOT / "engine_a5"\n        right_root = ROOT / "engine_a51"\n        left = {\n            str(path.relative_to(left_root))\n            for path in left_root.rglob("*")\n            if path.is_file()\n        }\n        right = {\n            str(path.relative_to(right_root))\n            for path in right_root.rglob("*")\n            if path.is_file()\n        }\n        self.assertEqual(left, right)\n\n    def test_base_a5_manifest_preserved(self):\n        manifest = json.loads(\n            (ROOT / "BASELINE_A5_MANIFEST_SHA256.json").read_text()\n        )\n        self.assertEqual(manifest["schema"], "belial_a5_16q_package_v1")\n\n\nif __name__ == "__main__":\n    unittest.main()\n')
# build worker
p=root/'a51/build_worker.py';s=p.read_text();
if 'engine_a5/Cargo.toml' not in s:raise SystemExit('A51_BUILD_WORKER_ENGINE_MARKER_MISSING')
p.write_text(s.replace('engine_a5/Cargo.toml','engine_a51/Cargo.toml'))
# install metadata
p=root/'a51/install.py';s=p.read_text();s=s.replace("RELEASE='belial-a5-16q-v1'","RELEASE='belial-a51-16q-9h-v1'").replace('.belial-a5-install.lock','.belial-a51-install.lock').replace('.belial-a4-staging-','.belial-a51-staging-').replace('BELIAL_A5_INSTALL_FAILED','BELIAL_A51_INSTALL_FAILED').replace('A4_RELEASE_REUSED_EXACT_HASHES','A51_RELEASE_REUSED_EXACT_HASHES').replace('A4_RELEASE_INSTALLED','A51_RELEASE_INSTALLED').replace('A5 1/3','A51 1/3').replace('A5 2/3','A51 2/3').replace('A5 3/3','A51 3/3').replace('90 minutos','9 horas').replace('Release A5 local','Release A51 local');p.write_text(s)
# paper worker optimization
p=root/'a51/paper_worker.py';s=p.read_text();marker='from a51.venue_worker import public_json,Denied\n';
if marker not in s:raise SystemExit('A51_PAPER_IMPORT_MARKER_MISSING')
s=s.replace(marker,marker+'from a51.cache import JsonCache\n',1)
old="session=Path(session);out=session/'paper';out.mkdir(exist_ok=True);stop=Stop().install()";new="session=Path(session);out=session/'paper';out.mkdir(exist_ok=True);stop=Stop().install();cache=JsonCache();heads=read_json(ROOT/'n2_a2/CONFIG_N2.json')['heads']";
if old not in s:raise SystemExit('A51_PAPER_INIT_MARKER_MISSING')
s=s.replace(old,new,1)
old_status="""            if t-last_status>=100:
                for s in SYMBOLS:
                    status=read_json(session/'sources'/s/'status.json',{}) or {}
                    analysis_bad=read_json(session/'ANALYSIS_BLOCKED.json',{}).get(s)
                    analysis_health=read_json(session/'aggregate/status.json',{}) or {}
                    if analysis_bad or (frozen and (analysis_health.get('state')!='ACTIVE' or t-analysis_health.get('at_ms',0)>5000)):
                        status=dict(status,state='QUARANTINED',reason=analysis_bad or 'ANALYSIS_NOT_ACTIVE',integrity=False)
                    if status!=seen_status.get(s):process('source_status',{'symbol':s,'status':status});seen_status[s]=status
                platform=read_json(session/'INTEGRITY.json',{'ok':True})
                if platform.get('ok') is not True and ctl.audit_ok:process('integrity',platform)
                last_status=t
"""
new_status="""            if t-last_status>=100:
                analysis_blocked=cache.get(session/'ANALYSIS_BLOCKED.json',{}) or {}
                analysis_health=cache.get(session/'aggregate/status.json',{}) or {}
                for s in SYMBOLS:
                    status=cache.get(session/'sources'/s/'status.json',{}) or {}
                    analysis_bad=analysis_blocked.get(s)
                    if analysis_bad or (frozen and (analysis_health.get('state')!='ACTIVE' or t-analysis_health.get('at_ms',0)>5000)):
                        status=dict(status,state='QUARANTINED',reason=analysis_bad or 'ANALYSIS_NOT_ACTIVE',integrity=False)
                    if status!=seen_status.get(s):process('source_status',{'symbol':s,'status':status});seen_status[s]=status
                platform=cache.get(session/'INTEGRITY.json',{'ok':True}) or {'ok':True}
                if platform.get('ok') is not True and ctl.audit_ok:process('integrity',platform)
                last_status=t
"""
if old_status not in s:raise SystemExit('A51_PAPER_STATUS_BLOCK_MISSING')
s=s.replace(old_status,new_status,1).replace("state=(read_json(session/'venues'/venue/'status.json',{}) or {}).get('state')","state=(cache.get(session/'venues'/venue/'status.json',{}) or {}).get('state')")
old_slow="""                snapshot.update(config=cfg,session=session.name,market_coverage=coverage,
                                main=read_json(session/'aggregate/m2_tail/state.json',{}),
                                shadow=read_json(session/'aggregate/shadow_compare/state.json',{}),
                                n2=read_json(session/'n2/status.json',{}),shadow_focus=read_json(session/'aggregate/shadow_focus/state.json',{}),heads=read_json(ROOT/'n2_a2/CONFIG_N2.json')['heads'])
"""
new_slow="""                snapshot.update(config=cfg,session=session.name,market_coverage=coverage,
                                main=cache.get(session/'aggregate/m2_tail/state.json',{}),
                                shadow=cache.get(session/'aggregate/shadow_compare/state.json',{}),
                                n2=cache.get(session/'n2/status.json',{}),
                                shadow_focus=cache.get(session/'aggregate/shadow_focus/state.json',{}),heads=heads)
"""
if old_slow not in s:raise SystemExit('A51_PAPER_SLOW_BLOCK_MISSING')
p.write_text(s.replace(old_slow,new_slow,1).replace('90-minute experiment','9-hour experiment').replace('if t-last_fast>=40:','if t-last_fast>=100:').replace('if t-last_slow>=250:','if t-last_slow>=500:'))
# supervisor hot path
p=root/'a51/supervisor.py';s=p.read_text();marker='from a51.book_health import classify_book\n';
if marker not in s:raise SystemExit('A51_SUPERVISOR_IMPORT_MARKER_MISSING')
s=s.replace(marker,marker+'from a51.cache import JsonCache\n',1)
pat=re.compile(r"    def __init__\(self,session,base=BASE\):\n.*?(?=    def launch\()",re.S)
rep="""    def __init__(self,session,base=BASE):
        self.session=Path(session);self.base=Path(base);self.procs={};self.logs={};self.stop=Stop().install();self.phase='PREPARING';self.stage='Inicializando';self.opened_url=None;self.ui_restarts=0;self.failures={};self.samples={s:dict(qualified_ms=0,ready_ms=0,book_ready_ms=0) for s in SYMBOLS};self.last_sample=None;self.cache=JsonCache();self.coverage_peak=dict(live_signal_sources=0,usable_books=0,paper_prerequisites_ready=0)
"""
s,n=pat.subn(rep,s,1)
if n!=1:raise SystemExit('A51_SUPERVISOR_INIT_PATCH_FAILED')
pat=re.compile(r"    def component_states\(self\):\n.*?(?=    def publish\()",re.S)
rep="""    def component_states(self):
        mapping={'build':'build','paper':'paper','spot_prepare':'spot_prep','raw':'raw','analysis':'aggregate','n2':'n2','rpc_relay':'rpc_relay','price_samples':'price_samples','ws_relay':'ws_relay','ui':'ui','satellites':'satellites'};states={}
        for name,p in self.procs.items():
            folder=mapping.get(name,'sources/'+name[7:] if name.startswith('sensor_') else name);row=dict(self.cache.get(self.session/folder/'status.json',{}) or {})
            if p.poll() is not None:
                complete=self.cache.get(self.session/folder/'COMPLETE.json',{}) or {};row.update(process_alive=False,returncode=p.returncode)
                if p.returncode!=0 or complete.get('ok') is False:row['state']='FAILED'
                elif row.get('state') not in ('SESSION_FINISHED','FINISHED','COMPLETE','STOPPED'):row['state']='FINISHED'
            else:row.update(process_alive=True,heartbeat_age_ms=max(0,now_ms()-row.get('at_ms',now_ms())))
            states[name]=row or {'state':'STARTING'}
        return states
"""
s,n=pat.subn(rep,s,1)
if n!=1:raise SystemExit('A51_SUPERVISOR_COMPONENT_PATCH_FAILED')
pat=re.compile(r"    def publish\(self\):\n.*?(?=    def setup\()",re.S)
rep="""    def publish(self):
        t=now_ms();cohort=self.cache.get(self.session/'COHORT.json');statuses={x:self.cache.get(self.session/'sources'/x/'status.json',{}) or {} for x in SYMBOLS};analysis=self.cache.get(self.session/'aggregate/status.json',{}) or {};blocked=self.cache.get(self.session/'ANALYSIS_BLOCKED.json',{}) or {}
        if self.phase=='CAPTURING':
            for symbol,status in list(statuses.items()):
                reason=blocked.get(symbol)
                if reason or analysis.get('state')!='ACTIVE' or t-analysis.get('at_ms',0)>5000:statuses[symbol]=dict(status,state='QUARANTINED',reason=reason or 'ANALYSIS_NOT_ACTIVE',integrity=False)
        integrity=self.cache.get(self.session/'INTEGRITY.json',{'ok':True}) or {'ok':True};books=self.cache.get(self.session/'paper/books.json',{}) or {};book_rows=[dict(r) for r in books.get('books',[])];elapsed=max(0,t-books.get('at_ms',t));venue_runtime=books.get('venue_runtime',{}) or {}
        for row in book_rows:
            transport=(venue_runtime.get(row.get('venue')) or {}).get('display_transport') or (books.get('transport') or {}).get(row.get('venue')) or 'UNKNOWN';display=classify_book(row,transport,elapsed,2000);row.update(display)
            if not display['ready_for_fills']:row['ready']=False
        coverage=summarize(cohort,statuses,book_rows,t,integrity.get('ok') is True)
        if cohort is None:
            caps=self.cache.get(self.session/'spot_prep/CAPABILITIES.json',{}) or initial()
            for row in coverage['rows']:row.update(caps.get(row['symbol'],{}))
        if self.phase=='CAPTURING':
            mono=time.monotonic_ns();dt=0 if self.last_sample is None else min(2000,(mono-self.last_sample)//1_000_000);self.last_sample=mono
            for row in coverage['rows']:
                symbol=row['symbol']
                if row.get('qualified'):self.samples[symbol]['qualified_ms']+=dt
                if row['signal_source_ready']:self.samples[symbol]['ready_ms']+=dt
                if row['venues_ready']:self.samples[symbol]['book_ready_ms']+=dt
            for key in self.coverage_peak:self.coverage_peak[key]=max(self.coverage_peak[key],int(coverage.get(key) or 0))
        label='Ainda não congelado' if cohort is None else ('16 leitores qualificados · cobertura temporal em medição' if cohort['integral_16q_at_start'] else f"COBERTURA PARCIAL · {len(cohort['qualified_at_start'])}/16 leitores qualificados")
        raw=self.cache.get(self.session/'raw/status.json',{}) or {};paper=self.cache.get(self.session/'paper/status.json',{}) or {};platform={'schema':'belial_a51_platform_v1','at_ms':t,'phase':self.phase,'stage':self.stage,'coverage':coverage,'coverage_peak':dict(self.coverage_peak),'experiment_label':label,'components':self.component_states(),'raw_counts':raw.get('raw_events',{}),'integrity':integrity,'paper_stale':t-paper.get('at_ms',0)>3000,'failures':self.failures,'cache':self.cache.view(),'orders':False};atomic(self.session/'PLATFORM.json',platform)
        ui=self.cache.get(self.session/'ui/URL.json',{}) or {}
        if ui.get('url') and ui['url']!=self.opened_url:
            self.opened_url=ui['url'];print('DASHBOARD_URL='+self.opened_url,flush=True)
            if not os.environ.get('BELIAL_A1_NO_BROWSER') and (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
                def open_page(url):
                    with contextlib.suppress(Exception):subprocess.run(['xdg-open',url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8)
                __import__('threading').Thread(target=open_page,args=(self.opened_url,),daemon=True).start()
        if 'ui' in self.procs and self.procs['ui'].poll() is not None and self.phase not in ('FINISHED','STOPPING') and self.ui_restarts<3:self.logs['ui'].close();del self.procs['ui'];self.ui_restarts+=1;self.launch('ui','ui_worker')
        return platform
"""
s,n=pat.subn(rep,s,1)
if n!=1:raise SystemExit('A51_SUPERVISOR_PUBLISH_PATCH_FAILED')
old="for d in ('logs','paper','aggregate','sources','ui','n2','build','price_samples','ws_relay'):";new="for d in ('logs','paper','aggregate','sources','ui','n2','build','price_samples','ws_relay','satellites'):"
if old not in s:raise SystemExit('A51_SUPERVISOR_DIR_MARKER_MISSING')
s=s.replace(old,new,1);old="self.launch('ui','ui_worker');self.publish()";new="self.launch('ui','ui_worker');self.launch('satellites','satellite_worker');self.publish()"
if old not in s:raise SystemExit('A51_SUPERVISOR_UI_LAUNCH_MARKER_MISSING')
s=s.replace(old,new,1).replace("self.stage='90 minutos · sessão nova · PAPER/shadow'","self.stage='9 horas · sessão nova · PAPER/shadow'")
old="print('BELIAL_A5_CAPTURE_STARTED=true; MINUTES=90; REQUESTED=16; QUALIFIED='+str(len(cohort['qualified_at_start']))+'; ORDERS=false',flush=True)";new="print('BELIAL_A51_CAPTURE_STARTED=true; MINUTES='+str(MINUTES)+'; REQUESTED=16; QUALIFIED='+str(len(cohort['qualified_at_start']))+'; ORDERS=false',flush=True)"
if old not in s:raise SystemExit('A51_SUPERVISOR_CAPTURE_PRINT_MARKER_MISSING')
s=s.replace(old,new,1).replace("'minutes_requested':90","'minutes_requested':MINUTES").replace("'coverage_sampled_ms':self.samples,'coverage_is_supervisor_sampled':True","'coverage_sampled_ms':self.samples,'coverage_peak':dict(self.coverage_peak),'coverage_is_supervisor_sampled':True").replace("html=(ROOT/'a4/web/dashboard.html').read_text()","html=(ROOT/'a51/web/dashboard.html').read_text()")
s=s.replace("('AGENT_STATUS',read_json(self.session/'n2/status.json',{}) or {})","('AGENT_STATUS',read_json(self.session/'n2/status.json',{}) or {}),('SATELLITES',read_json(self.session/'satellites/state.json',{}) or {})")
s=s.replace('BELIAL_A5_90M_RESULTADO_','BELIAL_A51_9H_RESULTADO_').replace("removeprefix('m2-a4-')","removeprefix('m2-a51-')").replace("runtime=BASE/'runtime/belial-a4'","runtime=BASE/'runtime/belial-a51'").replace("or b'a51.supervisor' in raw:raise SystemExit","or b'a5.supervisor' in raw or b'a51.supervisor' in raw:raise SystemExit").replace("BASE/'evidence'/('m2-a4-'+stamp)","BASE/'evidence'/('m2-a51-'+stamp)").replace('A4 exige uma pasta de sessão nova','A51 exige uma pasta de sessão nova').replace('A4_SUPERVISOR_ERROR','A51_SUPERVISOR_ERROR').replace('A4 não iniciou outra captura','A51 não iniciou outra captura').replace('A4 ZIP','A51 ZIP').replace("package_result(session,Path.home()/'Área de trabalho/Altside')","package_result(session,BASE/'evidence'/'results')").replace('A4 ENCERRANDO','A51 ENCERRANDO').replace('A4 REPLAY','A51 REPLAY').replace('A5 PREPARAÇÃO','A51 PREPARAÇÃO').replace('A5 FINALIZAÇÃO','A51 FINALIZAÇÃO').replace("print(f'A5 | restante=","print(f'A51 | restante=").replace('"""Local A4 supervisor.','"""Local A5.1 supervisor.')
# Display/supervisor sampling cadence only; decision processing remains unchanged.
old_capture_sleep="            time.sleep(.2)\n    def finish(self):"
new_capture_sleep="            time.sleep(.5)\n    def finish(self):"
if old_capture_sleep not in s:raise SystemExit('A51_CAPTURE_CADENCE_MARKER_MISSING')
s=s.replace(old_capture_sleep,new_capture_sleep,1)
p.write_text(s)
# dashboard
p=root/'a51/web/dashboard.html';h=p.read_text();h=h.replace('<title>BELIAL A4 | Terminal de pesquisa</title>','<title>BELIAL A5.1 | Terminal de pesquisa</title>').replace('A5 · 16Q RESEARCH TERMINAL','A5.1 · 16Q · SAT SHADOW').replace('id="clock">01:30:00','id="clock">09:00:00').replace('BELIAL A4 / RESEARCH','BELIAL A5.1 / RESEARCH').replace('BELIAL_A4_ESTADO_TELA_','BELIAL_A51_ESTADO_TELA_').replace(':5400000;',':32400000;').replace('não é uma captura A4','não é uma captura A5.1').replace('A4 e não está conectada','A5.1 e não está conectada')
marker='<span>N2 <strong id="n2Ribbon">SEM EVIDÊNCIA</strong></span>'
if marker not in h:raise SystemExit('A51_DASHBOARD_STATUS_MARKER_MISSING')
h=h.replace(marker,marker+'<span>SAT <strong id="satRibbon">—</strong></span>',1)
marker='<section class="panel"><div class="panel-title">16 FONTES / CAPACIDADES POR ATIVO'
if marker not in h:raise SystemExit('A51_DASHBOARD_SOURCE_MARKER_MISSING')
panel='<section class="panel" style="margin-bottom:9px"><div class="panel-title">SATÉLITES SHADOW · ATIVO SELECIONADO<span class="right">Não cria nem bloqueia trades</span></div><div class="property" id="satelliteSummary"></div><div class="tablewrap"><table class="datatable"><thead><tr><th>Venue</th><th>Estado</th><th class="num">Mid</th><th class="num">Ret. 10s</th><th class="num">Funding bruto</th><th class="num">Idade</th></tr></thead><tbody id="satelliteRows"></tbody></table></div><div class="footnote">Binance / Bybit / Kraken são contexto externo SHADOW. Funding e OI não são agregados entre venues porque unidades/intervalos podem divergir.</div></section>'
h=h.replace(marker,panel+marker,1)
old="const D={state:{},books:{},platform:{},agent:{},agent_status:{},sampler:{},ws_relay:{},ui:{}};"
if old not in h:raise SystemExit('A51_DASHBOARD_D_MARKER_MISSING')
h=h.replace(old,"const D={state:{},books:{},platform:{},agent:{},agent_status:{},sampler:{},ws_relay:{},satellites:{},ui:{}};",1)
satjs="function renderSatellites(){const sat=D.satellites||{},row=sat.by_symbol?.[U.symbol]||{},state=row.state||sat.state||'—',cls=state==='CONFIRMED'?'good':state==='DIVERGENT'?'bad':state==='NEUTRAL'?'warning':'muted';text('satRibbon',state,cls);props('satelliteSummary',[['Ativo',U.symbol],['Estado',state],['Direção',row.direction||'—'],['Concordância',num(row.agreement_count)+' / 3'],['Venues frescas',num(row.fresh_count)+' / 3'],['Divergência de preço',bp(row.price_divergence_bps)]]);const venues=['binance','bybit','kraken'].map(name=>{const v=row.venues?.[name]||{};return{name,...v,age:typeof v.at_ms==='number'?Math.max(0,Date.now()-v.at_ms):null};});rows('satelliteRows',venues,[v=>v.name.toUpperCase(),v=>({text:v.supported?(v.fresh?'FRESH':'STALE'):'SEM MERCADO',cls:v.fresh?'good':v.supported?'warning':'muted'}),v=>({text:price(v.mid),cls:'num'}),v=>({text:bp(v.return_10s_bps),cls:'num'}),v=>({text:isnum(v.funding_rate)?num(v.funding_rate,8):'—',cls:'num'}),v=>({text:isnum(v.age)?num(v.age,0)+' ms':'—',cls:'num'})],v=>v.name);}"
if 'function renderSources(){' not in h:raise SystemExit('A51_DASHBOARD_RENDER_SOURCES_MARKER_MISSING')
h=h.replace('function renderSources(){',satjs+'function renderSources(){',1)
if 'function renderSlow(){const cov=D.platform.coverage||{};' not in h:raise SystemExit('A51_DASHBOARD_RENDER_SLOW_MARKER_MISSING')
h=h.replace('function renderSlow(){const cov=D.platform.coverage||{};',"function renderSlow(){renderSatellites();const cov=D.platform.coverage||{};",1)
marker="ingest('agent_status',window.BELIAL_FINAL_AGENT_STATUS||{});"
if marker not in h:raise SystemExit('A51_DASHBOARD_FINAL_MARKER_MISSING')
h=h.replace(marker,marker+"ingest('satellites',window.BELIAL_FINAL_SATELLITES||{});",1);p.write_text(h)
# preserve full A5 base
def digest(path):
    x=hashlib.sha256();
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):x.update(b)
    return x.hexdigest()
baseline=json.loads((root/'BASELINE_A5_MANIFEST_SHA256.json').read_text())
for rel,expected in baseline['files'].items():
    p=root/rel
    if not p.is_file():raise SystemExit('A51_BASE_A5_MEMBER_MISSING:'+rel)
    if digest(p)!=expected:raise SystemExit('A51_BASE_A5_MUTATED:'+rel)
preservation={'schema':'belial_a51_preservation_v1','base_commit':base_commit,'base_release':'belial-a5-16q-v1','base_manifest_sha256':digest(root/'BASELINE_A5_MANIFEST_SHA256.json'),'a5_manifest_members_preserved':len(baseline['files']),'a5_base_byte_identical':True,'strategy_thresholds_changed':False,'sizing_changed':False,'exit_policies_changed':False,'orders':False,'paper':True,'shadow':True,'satellites_affect_trading':False,'duration_ms':32_400_000,'minutes':540,'cleanup_scope':['json cache','paper hot-path read hoist','bounded/coalesced UI','rustfmt/clippy/test/build','read-only satellites']}
(root/'docs/PRESERVATION_A51.json').write_text(json.dumps(preservation,indent=2,sort_keys=True)+'\n')
for p in sorted((root/'a51').rglob('*.py'))+sorted((root/'tests_a51').rglob('*.py')):compile(p.read_text(),str(p),'exec')
print('A51_DERIVATION=PASS');print('A51_A5_BASE_BYTE_IDENTICAL=true');print('A51_PAPER_BOOK_VIEW_HZ=10');print('A51_PAPER_STATE_VIEW_HZ=2');print('A51_PLATFORM_VIEW_HZ=2')

PY

# ---------------------------------------------------------------------------
# 3. Python + offline contracts
# ---------------------------------------------------------------------------
echo "[2b/12] Preflight semântico dos contratos A5.1"

python3 - "$WORK" <<'PY'
from pathlib import Path
import ast
import sys

root = Path(sys.argv[1])
path = root / "tests_a51/test_contract.py"
source = path.read_text(encoding="utf-8")
tree = ast.parse(source, filename=str(path))

imports = set()
for node in tree.body:
    if isinstance(node, ast.Import):
        imports.update(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        imports.add(node.module or "")

if "http.client" not in imports:
    raise SystemExit("A51_TEST_HTTP_CLIENT_IMPORT_MISSING")

classes = {
    node.name: node
    for node in tree.body
    if isinstance(node, ast.ClassDef)
}
klass = classes.get("A51ContractTests")
if klass is None:
    raise SystemExit("A51_CONTRACT_CLASS_MISSING")

methods = {
    node.name
    for node in klass.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
required = {
    "test_satellite_worker_is_read_only_shadow",
    "test_ui_health_is_read_only",
    "test_ui_bus_coalesces_identical_payload",
}
missing = sorted(required - methods)
if missing:
    raise SystemExit("A51_CONTRACT_METHODS_MISSING:" + repr(missing))

# The V1 failure came from a formatter-dependent literal-source assertion.
if "'affects_trading':False" in source and "assertIn" in source:
    raise SystemExit("A51_BRITTLE_SATELLITE_ASSERTION_PRESENT")

print("A51_CONTRACT_AST_PREFLIGHT=PASS")
print("A51_HTTP_CLIENT_IMPORT=PASS")
print("A51_SATELLITE_SEMANTIC_ASSERTIONS=PASS")
PY

echo "[3/12] Python compileall + contratos A5/A5.1"
mkdir -p "$EVIDENCE/python-cache" "$EVIDENCE/tests"
PYTHONPYCACHEPREFIX="$EVIDENCE/python-cache" \
  python3 -B -m compileall -q -f "$WORK/a51" "$WORK/tests_a51"

echo "A51_PYTHON_COMPILEALL=PASS"

(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m a51.satellite_worker --self-test
) | tee "$EVIDENCE/satellite-self-test.log"
grep -Fq "A51_SATELLITE_SELF_TEST=PASS" "$EVIDENCE/satellite-self-test.log" || fail "SATELLITE_SELF_TEST_FAILED"

# Base regression: A5 byte-identical code must still pass A5 tests.
(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m unittest discover -s tests_a5 -v
) >"$EVIDENCE/tests/a5-regression.log" 2>&1 || {
  tail -n 320 "$EVIDENCE/tests/a5-regression.log" >&2
  fail "A5_REGRESSION_FAILED"
}
echo "A5_REGRESSION=PASS"

(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m unittest discover -s tests_a51 -v
) >"$EVIDENCE/tests/a51-preseal.log" 2>&1 || {
  tail -n 320 "$EVIDENCE/tests/a51-preseal.log" >&2
  fail "A51_PRESEAL_TESTS_FAILED"
}
echo "A51_PRESEAL_TESTS=PASS"

# ---------------------------------------------------------------------------
# 4. Rust cleanup/compiler gates
# ---------------------------------------------------------------------------
echo "[4/12] Rust fmt + check + clippy + test/build"

cargo fmt --all --manifest-path "$WORK/engine_a51/Cargo.toml"
cargo fmt --all --check --manifest-path "$WORK/engine_a51/Cargo.toml"
echo "A51_RUST_FMT=PASS"

mkdir -p "$TARGET"
export CARGO_TARGET_DIR="$TARGET"
export CARGO_NET_OFFLINE=true
export TOKIO_WORKER_THREADS=1

cargo check --release --locked --offline --jobs 1 \
  --manifest-path "$WORK/engine_a51/Cargo.toml" \
  >"$EVIDENCE/rust-check.log" 2>&1 || {
  tail -n 280 "$EVIDENCE/rust-check.log" >&2
  fail "RUST_CHECK_FAILED"
}
echo "A51_RUST_CHECK=PASS"

if cargo clippy --version >/dev/null 2>&1; then
  cargo clippy --release --locked --offline --jobs 1 \
    --manifest-path "$WORK/engine_a51/Cargo.toml" \
    >"$EVIDENCE/rust-clippy.log" 2>&1 || {
    tail -n 280 "$EVIDENCE/rust-clippy.log" >&2
    fail "RUST_CLIPPY_FAILED"
  }
  echo "A51_RUST_CLIPPY=PASS"
else
  echo "A51_RUST_CLIPPY=NOT_AVAILABLE"
fi

for BIN in belial-perp-feed belial-spot-raw belial-r5c2-live-profiler; do
  cargo test --release --locked --offline --jobs 1 \
    --manifest-path "$WORK/engine_a51/Cargo.toml" --bin "$BIN" \
    >"$EVIDENCE/rust-test-$BIN.log" 2>&1 || {
    tail -n 280 "$EVIDENCE/rust-test-$BIN.log" >&2
    fail "RUST_TEST_FAILED:$BIN"
  }
  cargo build --release --locked --offline --jobs 1 \
    --manifest-path "$WORK/engine_a51/Cargo.toml" --bin "$BIN" \
    >"$EVIDENCE/rust-build-$BIN.log" 2>&1 || {
    tail -n 280 "$EVIDENCE/rust-build-$BIN.log" >&2
    fail "RUST_BUILD_FAILED:$BIN"
  }
done
echo "A51_RUST_TEST_BUILD=PASS"

# ---------------------------------------------------------------------------
# 5. Seal and verify package
# ---------------------------------------------------------------------------
echo "[5/12] Selando pacote A5.1"
python3 - "$WORK" <<'PY'
from pathlib import Path
import hashlib,json,sys
root=Path(sys.argv[1]);manifest=root/'MANIFEST_SHA256.json';files={}
for path in sorted(root.rglob('*')):
    if not path.is_file():continue
    rel=str(path.relative_to(root))
    if rel=='MANIFEST_SHA256.json':continue
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    files[rel]=h.hexdigest()
doc={'schema':'belial_a51_16q_9h_package_v1','base_commit':'eb1beadeedd0f81f8ab1b882ac86c5fc39332386','base_release':'belial-a5-16q-v1','requested_assets':16,'duration_ms':32_400_000,'minutes':540,'satellites':['binance','bybit','kraken'],'satellites_shadow_only':True,'satellites_affect_trading':False,'orders':False,'files':files}
manifest.write_text(json.dumps(doc,indent=2,sort_keys=True)+'\n')
print('A51_MANIFEST_FILES='+str(len(files)))
PY

(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m a51.verify --log-dir "$EVIDENCE/tests"
) | tee "$EVIDENCE/a51-verify.log"
grep -Fq "A51_PACKAGE_HASHES=PASS" "$EVIDENCE/a51-verify.log" || fail "A51_PACKAGE_VERIFY_FAILED"

# ---------------------------------------------------------------------------
# 6. Immutable release
# ---------------------------------------------------------------------------
echo "[6/12] Instalando release imutável"
if [ -e "$DEST" ]; then
  if diff -qr -- "$WORK" "$DEST" >"$EVIDENCE/release-reuse.diff" 2>&1; then
    echo "A51_RELEASE_REUSED_EXACT=true"
  else
    cat "$EVIDENCE/release-reuse.diff" >&2
    fail "EXISTING_A51_RELEASE_DIFFERS"
  fi
else
  STAGING="$BASE/releases/.belial-a51-staging-$TS"
  cp -a "$WORK" "$STAGING"
  (
    cd "$STAGING"
    PYTHONPATH="$STAGING" python3 -B -m a51.verify --hashes-only
  ) >"$EVIDENCE/release-verify.log" 2>&1 || {
    cat "$EVIDENCE/release-verify.log" >&2
    fail "A51_RELEASE_VERIFY_FAILED"
  }
  mv "$STAGING" "$DEST"
  echo "A51_RELEASE_INSTALLED=$DEST"
fi

# ---------------------------------------------------------------------------
# 7. Live public satellite probe — diagnostic only, never a strategy gate
# ---------------------------------------------------------------------------
echo "[7/12] Probe público Binance / Bybit / Kraken (SHADOW)"
PROBE="$EVIDENCE/satellite-probe"
mkdir -p "$PROBE"
(
  cd "$DEST"
  PYTHONPATH="$DEST" python3 -B -m a51.satellite_worker --session "$PROBE" --once
) >"$EVIDENCE/satellite-probe.log" 2>&1 || {
  cat "$EVIDENCE/satellite-probe.log" >&2
  fail "SATELLITE_PROBE_PROCESS_FAILED"
}

python3 - "$PROBE/satellites/state.json" <<'PY'
import json,sys
doc=json.load(open(sys.argv[1]))
print('SATELLITE_PROBE_STATE='+str(doc.get('state')))
for venue,row in (doc.get('venues') or {}).items():
    print('SATELLITE_'+venue.upper()+'='+str(row.get('state'))+' symbols='+str(row.get('symbols'))+' error='+str(row.get('error')))
if doc.get('orders') is not False or doc.get('affects_trading') is not False or doc.get('shadow_only') is not True:
    raise SystemExit('SATELLITE_SAFETY_CONTRACT_FAILED')
print('SATELLITE_SHADOW_CONTRACT=PASS')
PY

# ---------------------------------------------------------------------------
# 8. Git snapshot / handoff
# ---------------------------------------------------------------------------
echo "[8/12] Preparando checkpoint Git A5.1"
if [ -e "$NEW_SNAPSHOT" ]; then
  if ! diff -qr -- "$WORK" "$NEW_SNAPSHOT" >"$EVIDENCE/git-snapshot-reuse.diff" 2>&1; then
    cat "$EVIDENCE/git-snapshot-reuse.diff" >&2
    fail "EXISTING_GIT_A51_SNAPSHOT_DIFFERS"
  fi
else
  mkdir -p "$(dirname "$NEW_SNAPSHOT")"
  cp -a "$WORK" "$NEW_SNAPSHOT"
fi

mkdir -p "$(dirname "$INSTALLER_DEST")" "$REPO/docs"
cp -- "$SELF" "$INSTALLER_DEST"
chmod 0755 "$INSTALLER_DEST"

cat > "$REPO/docs/PERFORMANCE_BASELINE_A5_SMOKE25.json" <<'EOF'
{
  "schema": "belial_a5_smoke25_baseline_v1",
  "commit": "eb1beadeedd0f81f8ab1b882ac86c5fc39332386",
  "seconds": 1500,
  "software_gate_pass": true,
  "samples": 291,
  "rss_peak_mb": 871.7890625,
  "rss_median_mb": 844.69140625,
  "cpu_seconds_delta": 793.5400000000001,
  "cpu_seconds_per_wall_second": 0.5290266666666667,
  "messages_per_second_median": 101.0,
  "messages_per_second_p95": 182.0,
  "rust_to_paper_ms_p95_median": 8.972759,
  "rust_to_paper_ms_p95_peak": 17.256996,
  "ui_http_ms_p50": 30.15857600075833,
  "ui_http_ms_p95": 34.359661000053165,
  "ui_max_payload_bytes_peak": 128977.0,
  "ingest_queue_peak": 8.0,
  "ui_health_success_ratio": 1.0,
  "note": "Software baseline before A5.1 cleanup and external satellites. Final Smoke25 coverage was post-stop and is not used as peak coverage."
}
EOF

cat > "$REPO/docs/HANDOFF_A51_CLEAN_PERF_SATELLITES_9H_V2.md" <<EOF
# Bot Belial — A5.1 Clean/Perf + Satélites + 9H

Base congelada: \`$EXPECTED_HEAD\`

## Preservado

- A5 16Q original byte-idêntico;
- thresholds, sizing e políticas de saída inalterados;
- PAPER / SHADOW;
- orders=false.

## Limpeza / performance

- cache JSON mtime/size em status hot-path;
- ANALYSIS_BLOCKED e aggregate/status lidos uma vez por ciclo de status;
- N2 heads estáticos lidos uma vez;
- books.json de display: 10 Hz; state/platform: 2 Hz;
- curvas do painel limitadas a 240 pontos; histórico fechado a 60 posições;
- SSE coalesce payload serializado idêntico;
- dashboard A5.1 servido de \`a51/web/dashboard.html\`;
- Rust formatado e validado com check/clippy/test/build.

## Satélites

- Binance USDⓈ-M Futures: SHADOW;
- Bybit linear: SHADOW;
- Kraken Futures: SHADOW;
- somente endpoints públicos GET;
- sem API keys;
- sem ordens;
- não criam, bloqueiam, dimensionam ou encerram trades;
- last-good pode ser exibido como STALE; ausência de dado nunca vira zero;
- funding/OI brutos não são agregados entre venues com unidades/intervalos diferentes.

## Sessão

Após o checkpoint Git, o instalador inicia 540 minutos / 9 horas. Antes de
consumir as 9h, exige que o caminho principal tenha observado pelo menos um
sensor vivo, um livro utilizável e um conjunto de pré-requisitos PAPER.
EOF

cat > "$REPO/docs/CURRENT_STATE.md" <<EOF
# Bot Belial — Estado atual

## Linha

**BELIAL A5.1 16Q / 9H**

Base:

\`$EXPECTED_HEAD feat(a5): bootstrap isolated 16Q research line\`

## Segurança

- PAPER / SHADOW;
- orders=false;
- Binance/Bybit/Kraken somente satélites externos;
- satélites não participam do gate de trading.

## Performance

Baseline Smoke25: \`docs/PERFORMANCE_BASELINE_A5_SMOKE25.json\`.
A5.1 reduz polling/serialização de display sem alterar o ledger/decisão.

## Execução

Próxima sessão: 9 horas, 16 ativos, satélites SHADOW.
EOF

DEEP_LOG="$REPO/Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv"
if [ ! -f "$DEEP_LOG" ]; then
  printf 'utc\tphase\tstatus\trelease\tmanifest_sha256\tinstaller_sha256\n' > "$DEEP_LOG"
fi
if ! grep -Fq "A51_CLEAN_PERF_SATELLITES_9H" "$DEEP_LOG"; then
  printf '%s\tA51_CLEAN_PERF_SATELLITES_9H_V2\tLOCAL_GATES_PASS\t%s\t%s\t%s\n' \
    "$TS" "$RELEASE_NAME" \
    "$(sha256sum "$DEST/MANIFEST_SHA256.json" | awk '{print $1}')" \
    "$(sha256sum "$INSTALLER_DEST" | awk '{print $1}')" \
    >> "$DEEP_LOG"
fi

CHECKPOINT="$REPO/Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A51_CLEAN_PERF_SATELLITES_9H_V2.sha256"
(
  cd "$REPO"
  sha256sum \
    "$NEW_REL/MANIFEST_SHA256.json" \
    "$NEW_REL/a51/common.py" \
    "$NEW_REL/a51/cache.py" \
    "$NEW_REL/a51/satellite_worker.py" \
    "$NEW_REL/a51/ui_worker.py" \
    "$NEW_REL/a51/paper_worker.py" \
    "$NEW_REL/a51/supervisor.py" \
    "$NEW_REL/engine_a51/src/main.rs" \
    "$INSTALLER_REL" \
    docs/HANDOFF_A51_CLEAN_PERF_SATELLITES_9H_V2.md \
    docs/PERFORMANCE_BASELINE_A5_SMOKE25.json \
    docs/CURRENT_STATE.md \
    > "$CHECKPOINT"
)

# ---------------------------------------------------------------------------
# 9. Staged-tree proof / Git checkpoint
# ---------------------------------------------------------------------------
echo "[9/12] Staging + prova da árvore Git"
cd "$REPO"
git add -f -- "$NEW_REL"
git add -- \
  "$INSTALLER_REL" \
  "Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv" \
  "Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A51_CLEAN_PERF_SATELLITES_9H_V2.sha256" \
  "docs/HANDOFF_A51_CLEAN_PERF_SATELLITES_9H_V2.md" \
  "docs/PERFORMANCE_BASELINE_A5_SMOKE25.json" \
  "docs/CURRENT_STATE.md"

# Known byte-preserved whitespace debt inherited from A5/A4. No NEW A5.1
# whitespace error is allowed outside exactly these paths.
WS1=":(exclude)$NEW_REL/legacy/runner/tools/preflight_pools.py"
WS2=":(exclude)$NEW_REL/tests_a3/test_processes.py"
WS3=":(exclude)$NEW_REL/tests_a4/test_processes.py"
git diff --cached --check -- . "$WS1" "$WS2" "$WS3" >"$EVIDENCE/git-diff-check.log" 2>&1 || {
  cat "$EVIDENCE/git-diff-check.log" >&2
  fail "NEW_WHITESPACE_ERROR"
}

for REL in \
  "legacy/runner/tools/preflight_pools.py" \
  "tests_a3/test_processes.py" \
  "tests_a4/test_processes.py"; do
  cmp -s -- "$SOURCE/$REL" "$NEW_SNAPSHOT/$REL" || fail "PRESERVED_WHITESPACE_FILE_MUTATED:$REL"
done

TREE_SHA="$(git write-tree)"
INDEX_EXPORT="$TMP/index-export"
mkdir -p "$INDEX_EXPORT"
git archive --format=tar "$TREE_SHA" -- "$NEW_REL" | tar -xf - -C "$INDEX_EXPORT"
INDEX_A51="$INDEX_EXPORT/$NEW_REL"
(
  cd "$INDEX_A51"
  PYTHONPATH="$INDEX_A51" python3 -B -m a51.verify --hashes-only
) | tee "$EVIDENCE/a51-staged-index.log"
grep -Fq "A51_PACKAGE_HASHES=PASS" "$EVIDENCE/a51-staged-index.log" || fail "A51_STAGED_INDEX_VERIFY_FAILED"
echo "A51_STAGED_INDEX_CANONICAL=true"

# ---------------------------------------------------------------------------
# 10. Commit + push
# ---------------------------------------------------------------------------
echo "[10/12] Commit + push A5.1"
git commit -m "feat(a51): clean runtime and add shadow satellites for 9h"
A51_COMMIT="$(git rev-parse HEAD)"
git push origin main
git fetch origin main >/dev/null 2>&1 || true
REMOTE_AFTER="$(git rev-parse origin/main)"
[ "$REMOTE_AFTER" = "$A51_COMMIT" ] || fail "REMOTE_HEAD_MISMATCH_AFTER_PUSH"
[ -z "$(git status --porcelain=v1)" ] || fail "WORKTREE_NOT_CLEAN_AFTER_COMMIT"
echo "A51_GIT_CHECKPOINT=PASS"
echo "A51_COMMIT_SHA=$A51_COMMIT"

# Temp files are no longer needed after immutable release + Git checkpoint.
cleanup_tmp
trap - EXIT

# ---------------------------------------------------------------------------
# 11. Launch 9H and require a live primary market path before committing 9h
# ---------------------------------------------------------------------------
echo "[11/12] Iniciando sessão 9H"
[ ! -e "$SESSION" ] || fail "SESSION_ALREADY_EXISTS"
unset BELIAL_A1_NO_BROWSER || true

(
  cd "$DEST"
  exec env \
    PYTHONPATH="$DEST" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    python3 -B -m a51.supervisor --session "$SESSION"
) >"$SUP_LOG" 2>&1 &
SUP_PID=$!
SESSION_STARTED=1
# Any unexpected installer exit after launch requests a graceful stop.
trap 'stop_session' EXIT
echo "A51_SUPERVISOR_PID=$SUP_PID"

# Preparation is outside the 9-hour capture clock.
PREP_START="$(date +%s)"
while :; do
  if ! kill -0 "$SUP_PID" 2>/dev/null; then
    wait "$SUP_PID" || true
    tail -n 360 "$SUP_LOG" >&2 || true
    fail "SUPERVISOR_EXITED_BEFORE_CAPTURE"
  fi
  PHASE="$(python3 - "$SESSION" <<'PY'
from pathlib import Path
import json,sys
p=Path(sys.argv[1])/'SESSION.json'
try:print(json.loads(p.read_text()).get('phase',''))
except Exception:print('')
PY
)"
  [ "$PHASE" = "CAPTURING" ] && break
  NOW="$(date +%s)"
  if [ $((NOW-PREP_START)) -ge 1200 ]; then
    tail -n 360 "$SUP_LOG" >&2 || true
    fail "PREPARATION_TIMEOUT"
  fi
  sleep 2
done

echo "A51_9H_CAPTURE_STARTED=true"
python3 - "$SESSION/ui/URL.json" <<'PY'
import json,sys
try:print('DASHBOARD_URL='+json.load(open(sys.argv[1]))['url'])
except Exception:pass
PY

# Primary-path gate: satellites are explicitly NOT part of this gate.
MARKET_GATE_START="$(date +%s)"
while :; do
  if ! kill -0 "$SUP_PID" 2>/dev/null; then
    wait "$SUP_PID" || true
    tail -n 360 "$SUP_LOG" >&2 || true
    fail "SUPERVISOR_EXITED_DURING_MARKET_GATE"
  fi
  GATE="$(python3 - "$SESSION/PLATFORM.json" <<'PY'
import json,sys
try:
 d=json.load(open(sys.argv[1]));p=d.get('coverage_peak') or {};print(f"{int(p.get('live_signal_sources') or 0)} {int(p.get('usable_books') or 0)} {int(p.get('paper_prerequisites_ready') or 0)}")
except Exception:print('0 0 0')
PY
)"
  read -r LIVE BOOKS READY <<<"$GATE"
  if [ "$LIVE" -gt 0 ] && [ "$BOOKS" -gt 0 ] && [ "$READY" -gt 0 ]; then
    echo "A51_PRIMARY_MARKET_PATH=PASS live=$LIVE books=$BOOKS ready=$READY"
    break
  fi
  NOW="$(date +%s)"
  if [ $((NOW-MARKET_GATE_START)) -ge 180 ]; then
    echo "A51_PRIMARY_MARKET_PATH=FAIL live=$LIVE books=$BOOKS ready=$READY"
    kill -INT "$SUP_PID" 2>/dev/null || true
    tail -n 360 "$SUP_LOG" >&2 || true
    fail "PRIMARY_MARKET_PATH_NOT_READY_AFTER_180S"
  fi
  sleep 5
done

# ---------------------------------------------------------------------------
# 12. Lightweight profiler until natural 9H finalization
# ---------------------------------------------------------------------------
echo "[12/12] 9H em execução + profiler leve"

profile_once(){
python3 - "$SUP_PID" "$SESSION" "$PERF" <<'PY'
from pathlib import Path
import json,os,sys,time,urllib.request
root_pid=int(sys.argv[1]);session=Path(sys.argv[2]);out=Path(sys.argv[3])
def read(path,default=None):
    try:return json.loads(Path(path).read_text())
    except Exception:return default
rows={}
for path in Path('/proc').glob('[0-9]*'):
    try:
        status={}
        for line in (path/'status').read_text(errors='replace').splitlines():
            if ':' in line:
                k,v=line.split(':',1);status[k]=v.strip()
        pid=int(path.name);ppid=int(status.get('PPid','0'));rss=int(status.get('VmRSS','0 kB').split()[0])
        raw=(path/'stat').read_text();end=raw.rfind(')');fields=raw[end+2:].split();hz=os.sysconf(os.sysconf_names['SC_CLK_TCK']);cpu=(int(fields[11])+int(fields[12]))/hz
        rows[pid]={'pid':pid,'ppid':ppid,'rss_kb':rss,'cpu_seconds':cpu}
    except Exception:pass
desc={root_pid};changed=True
while changed:
    changed=False
    for pid,row in rows.items():
        if pid not in desc and row['ppid'] in desc:desc.add(pid);changed=True
procs=[rows[p] for p in desc if p in rows]
books=read(session/'paper/books.json',{}) or {};ui=read(session/'ui/status.json',{}) or {};platform=read(session/'PLATFORM.json',{}) or {};sat=read(session/'satellites/state.json',{}) or {};integrity=read(session/'INTEGRITY.json',{'ok':True}) or {'ok':True}
url=(read(session/'ui/URL.json',{}) or {}).get('url');http_ms=None;http_ok=False
if url:
    started=time.perf_counter()
    try:
        with urllib.request.urlopen(url+'health',timeout=.8) as r:r.read();http_ok=r.status==200
    except Exception:pass
    http_ms=(time.perf_counter()-started)*1000
row={'at_ms':time.time_ns()//1_000_000,'process_count':len(procs),'rss_kb':sum(x['rss_kb'] for x in procs),'cpu_seconds':sum(x['cpu_seconds'] for x in procs),'messages_per_second':books.get('messages_per_second'),'rust_to_paper_ms_p95':books.get('rust_to_paper_ms_p95'),'ingest_queue':books.get('ingest_queue'),'ui_max_payload_bytes':ui.get('maximum_payload_bytes'),'ui_coalesced_payloads':ui.get('coalesced_payloads'),'ui_http_ms':http_ms,'ui_http_ok':http_ok,'coverage':platform.get('coverage'),'coverage_peak':platform.get('coverage_peak'),'cache':platform.get('cache'),'satellite_state':sat.get('state'),'satellite_venues':sat.get('venues'),'integrity_ok':integrity.get('ok',True)}
with out.open('a',encoding='utf-8') as f:f.write(json.dumps(row,separators=(',',':'),sort_keys=True)+'\n')
if integrity.get('ok') is False:raise SystemExit(11)
PY
}

LAST_PRINT=0
while kill -0 "$SUP_PID" 2>/dev/null; do
  set +e
  profile_once
  PROFILE_RC=$?
  set -e
  if [ "$PROFILE_RC" -eq 11 ]; then echo "A51_INTEGRITY_FAILED_DURING_9H=true"; fi
  NOW="$(date +%s)"
  if [ $((NOW-LAST_PRINT)) -ge 300 ]; then
    python3 - "$SESSION" <<'PY'
from pathlib import Path
import json,sys,time
s=Path(sys.argv[1])
def read(p):
    try:return json.loads(p.read_text())
    except Exception:return {}
session=read(s/'SESSION.json');platform=read(s/'PLATFORM.json');sat=read(s/'satellites/state.json');deadline=session.get('deadline_ms');now=time.time_ns()//1_000_000;left=max(0,(deadline-now)//1000) if isinstance(deadline,int) else None
print('A51_9H_PROGRESS left_s='+str(left)+' coverage_peak='+repr(platform.get('coverage_peak'))+' satellites='+str(sat.get('state')))
PY
    LAST_PRINT=$NOW
  fi
  sleep 30
done

set +e
wait "$SUP_PID"
SUP_RC=$?
set -e
SESSION_STARTED=0
trap - EXIT
echo "A51_SUPERVISOR_RC=$SUP_RC"

python3 - "$SESSION" "$PERF" "$FINAL_REPORT" "$A51_COMMIT" <<'PY'
from pathlib import Path
import json,statistics,sys
session=Path(sys.argv[1]);perf=Path(sys.argv[2]);out=Path(sys.argv[3]);commit=sys.argv[4]
def read(path,default=None):
    try:return json.loads(Path(path).read_text())
    except Exception:return default
summary=read(session/'SUMMARY.json',{}) or {};replay=read(session/'REPLAY_CHECK.json',{}) or {};paper=read(session/'paper/COMPLETE.json',{}) or {};integrity=read(session/'INTEGRITY.json',{}) or {};satellites=read(session/'satellites/state.json',{}) or {}
samples=[]
if perf.exists():
    for line in perf.read_text().splitlines():
        try:samples.append(json.loads(line))
        except Exception:pass
def values(key):return [float(r[key]) for r in samples if isinstance(r.get(key),(int,float)) and not isinstance(r.get(key),bool)]
def p95(xs):
    if not xs:return None
    xs=sorted(xs);return xs[int((len(xs)-1)*.95)]
rss=values('rss_kb');cpu=values('cpu_seconds');lat=values('rust_to_paper_ms_p95');http=values('ui_http_ms');payload=values('ui_max_payload_bytes');mps=values('messages_per_second');ats=values('at_ms')
wall=(ats[-1]-ats[0])/1000 if len(ats)>=2 else None
failures=summary.get('failures') or {};critical={k:v for k,v in failures.items() if k in {'supervisor','paper','analysis','n2','disk'}};peak=summary.get('coverage_peak') or {}
software_ok=all([summary.get('capture_started') is True,summary.get('orders') is False,summary.get('interrupted') is False,(summary.get('capture_elapsed_ms') or 0)>=32_300_000,integrity.get('ok') is True,replay.get('output_matches_live') is True,paper.get('ok') is True,not critical,int(peak.get('live_signal_sources') or 0)>0,int(peak.get('usable_books') or 0)>0,int(peak.get('paper_prerequisites_ready') or 0)>0])
report={'schema':'belial_a51_9h_report_v1','commit':commit,'software_gate_pass':software_ok,'orders':False,'paper':True,'shadow':True,'duration_requested_ms':32_400_000,'capture_elapsed_ms':summary.get('capture_elapsed_ms'),'coverage_peak':peak,'coverage_sampled_ms':summary.get('coverage_sampled_ms'),'satellites_final':satellites,'integrity':integrity,'replay':replay,'paper_complete':paper,'critical_failures':critical,'all_failures':failures,'performance':{'samples':len(samples),'rss_peak_mb':max(rss)/1024 if rss else None,'rss_median_mb':statistics.median(rss)/1024 if rss else None,'cpu_seconds_delta':cpu[-1]-cpu[0] if len(cpu)>=2 else None,'wall_seconds_sampled':wall,'cpu_seconds_per_wall_second':(cpu[-1]-cpu[0])/wall if len(cpu)>=2 and wall else None,'messages_per_second_median':statistics.median(mps) if mps else None,'rust_to_paper_ms_p95_median':statistics.median(lat) if lat else None,'rust_to_paper_ms_p95_peak':max(lat) if lat else None,'ui_http_ms_p50':statistics.median(http) if http else None,'ui_http_ms_p95':p95(http),'ui_max_payload_bytes_peak':max(payload) if payload else None,'ui_health_success_ratio':sum(1 for r in samples if r.get('ui_http_ok') is True)/len(samples) if samples else None},'baseline_smoke25_reference':{'rss_peak_mb':871.7890625,'rss_median_mb':844.69140625,'cpu_seconds_per_wall_second':0.5290266666666667,'rust_to_paper_ms_p95_median':8.972759,'ui_http_ms_p95':34.359661000053165,'ui_max_payload_bytes_peak':128977.0}}
out.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
print('A51_9H_SOFTWARE_GATE_PASS='+str(software_ok).lower());print('A51_9H_COVERAGE_PEAK='+repr(peak));print('A51_9H_PERFORMANCE='+repr(report['performance']));print('A51_9H_REPORT='+str(out))
if not software_ok:raise SystemExit(2)
PY

echo
echo "============================================================"
echo " BELIAL A5.1 16Q | 9H = FINALIZADO"
echo "============================================================"
echo "A51_COMMIT_SHA=$A51_COMMIT"
echo "A51_SESSION=$SESSION"
echo "A51_REPORT=$FINAL_REPORT"
echo "ORDERS=false"
