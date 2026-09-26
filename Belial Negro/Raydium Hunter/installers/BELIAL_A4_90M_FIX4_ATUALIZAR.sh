#!/usr/bin/env bash
set -euo pipefail
umask 077

# BELIAL A4 FIX4
# Gate harness revision: CANONICAL_GIT_R2
# Source validation uses git archive HEAD; verifier remains strict.
# Objetivo:
#   1) preservar A4 congelado com 24 símbolos;
#   2) restaurar engine_a4/src/raydium_protocols.rs byte-a-byte do engine_a2;
#   3) manter o único delta nativo em engine_a4/src/main.rs;
#   4) executar todas as suítes offline + gates Rust nativos;
#   5) instalar release imutável belial-a4-90m-v1-fix4;
#   6) preparar snapshot + evidência + handoff para commit Git.
#
# PAPER / SHADOW SOMENTE. Nenhuma ordem real.
#
# Uso:
#   bash BELIAL_A4_90M_FIX4_ATUALIZAR.sh
#   bash BELIAL_A4_90M_FIX4_ATUALIZAR.sh --verificar-pacote
#
# Este script NÃO faz git commit/push automaticamente.
# Depois de todos os gates verdes ele imprime os comandos exatos para Git.

MODE="${1:-}"
if [ "$#" -gt 1 ] || { [ -n "$MODE" ] && [ "$MODE" != "--verificar-pacote" ]; }; then
  echo "Uso: bash BELIAL_A4_90M_FIX4_ATUALIZAR.sh [--verificar-pacote]" >&2
  exit 2
fi

if [ "$(id -u)" -eq 0 ]; then
  echo "Execute como usuário normal, sem sudo/root." >&2
  exit 2
fi

for cmd in git python3 sha256sum find diff cp mkdir awk sed grep cargo findmnt tee tail mv date readlink chmod tar; do
  command -v "$cmd" >/dev/null || {
    echo "Dependência ausente: $cmd" >&2
    exit 2
  }
done

BASE="${BELIAL_BASE:-/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter}"
GIT_REPO="${BELIAL_GIT_REPO:-$HOME/Área de trabalho/belial-github-publicar}"
SOURCE_REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a4-90m-v1-fix1"
SOURCE="$GIT_REPO/$SOURCE_REL"
NEW_REL="Belial Negro/Raydium Hunter/source-snapshots/belial-a4-90m-v1-fix4"
NEW_SNAPSHOT="$GIT_REPO/$NEW_REL"
INSTALLER_REL="Belial Negro/Raydium Hunter/installers/BELIAL_A4_90M_FIX4_ATUALIZAR.sh"
INSTALLER_DEST="$GIT_REPO/$INSTALLER_REL"
RELEASE_NAME="belial-a4-90m-v1-fix4"
RELEASES="$BASE/releases"
DEST="$RELEASES/$RELEASE_NAME"
TARGET="$BASE/target-belial-a4-fix4"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE="$BASE/evidence/a4-fix4/$TS"
BACKUP="$BASE/.belial-backups/a4-fix4-$TS"
SELF="$(readlink -f "$0")"
TMP="$(mktemp -d -t belial-a4-fix4-XXXXXX)"
WORK="$TMP/BELIAL_A4_90M_FIX4"

cleanup() {
  rm -rf -- "$TMP"
}
trap cleanup EXIT

fail() {
  echo
  echo "BELIAL_A4_FIX4_FAILED=$1" >&2
  exit 3
}

echo "============================================================"
echo " BELIAL A4 FIX4 | contrato congelado | PAPER / SHADOW"
echo "============================================================"
echo "BASE=$BASE"
echo "GIT_REPO=$GIT_REPO"
echo "SOURCE=$SOURCE"
echo "DEST=$DEST"
echo

findmnt -rn --mountpoint /mnt/sherlock-ssd >/dev/null || fail "SSD_NOT_MOUNTED"
[ -d "$BASE" ] || fail "BELIAL_BASE_NOT_FOUND"
[ -d "$GIT_REPO/.git" ] || fail "GIT_REPO_NOT_FOUND"
[ -d "$SOURCE" ] || fail "SOURCE_SNAPSHOT_NOT_FOUND"
[ -f "$SOURCE/MANIFEST_SHA256.json" ] || fail "SOURCE_MANIFEST_NOT_FOUND"

if find "$SOURCE" -type l -print -quit | grep -q .; then
  fail "SOURCE_CONTAINS_SYMLINK"
fi

mkdir -p "$EVIDENCE" "$BACKUP" "$RELEASES"

# -------- Gate 0: Git baseline / recuperação --------
echo "[0/8] Baseline Git e backup de metadados"

(
  cd "$GIT_REPO"
  git rev-parse HEAD > "$BACKUP/git-head-before.txt"
  git status --porcelain=v1 > "$BACKUP/git-status-before.txt"
  git log -10 --oneline --decorate > "$BACKUP/git-log-before.txt"
)

if [ -s "$BACKUP/git-status-before.txt" ]; then
  echo "O repositório Git local já possui alterações pendentes:"
  cat "$BACKUP/git-status-before.txt"
  fail "GIT_WORKTREE_NOT_CLEAN"
fi

# Fetch é informativo; não reseta nem mescla silenciosamente.
(
  cd "$GIT_REPO"
  if git fetch origin main >"$EVIDENCE/git-fetch.log" 2>&1; then
    LOCAL_HEAD="$(git rev-parse HEAD)"
    REMOTE_HEAD="$(git rev-parse origin/main)"
    printf 'LOCAL_HEAD=%s\nREMOTE_HEAD=%s\n' "$LOCAL_HEAD" "$REMOTE_HEAD" \
      > "$EVIDENCE/git-baseline.env"
    if [ "$LOCAL_HEAD" != "$REMOTE_HEAD" ]; then
      echo "HEAD local e origin/main diferem; recuso alterar o baseline." >&2
      cat "$EVIDENCE/git-baseline.env" >&2
      exit 31
    fi
  else
    echo "GIT_FETCH_WARNING=true" > "$EVIDENCE/git-baseline.env"
    echo "Fetch falhou; nenhum reset/merge foi executado." >> "$EVIDENCE/git-fetch.log"
  fi
) || fail "GIT_BASELINE_DIVERGED"

sha256sum \
  "$SOURCE/MANIFEST_SHA256.json" \
  "$SOURCE/engine_a2/src/main.rs" \
  "$SOURCE/engine_a2/src/raydium_protocols.rs" \
  "$SOURCE/engine_a4/src/main.rs" \
  "$SOURCE/engine_a4/src/raydium_protocols.rs" \
  > "$BACKUP/critical-source-before.sha256"

# -------- Gate 1: verifica snapshot CANONICO do Git sem mutacao --------
echo "[1/8] Verificando snapshot A4 canonico do Git"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export BELIAL_A1_NO_BROWSER=1

# A working tree pode conter artefatos ignorados gerados por validacoes antigas,
# por exemplo history_a*/validation/*.log. Esses arquivos NAO pertencem ao
# MANIFEST_SHA256.json e nao devem entrar no gate PACKAGE_MEMBERS_CHANGED.
#
# Nao apagamos os artefatos e nao relaxamos o verifier.
# Em vez disso, exportamos somente o conteudo rastreado pelo HEAD ja conferido
# no Gate 0. O a4.verify continua estrito sobre a copia canonica.
GIT_EXPORT="$TMP/git-export"
SOURCE_CANONICAL="$GIT_EXPORT/$SOURCE_REL"

mkdir -p "$GIT_EXPORT"

(
  cd "$GIT_REPO"

  git ls-files --others --ignored --exclude-standard -- "$SOURCE_REL"     > "$EVIDENCE/source-ignored-files.txt"

  git archive --format=tar HEAD -- "$SOURCE_REL"
) | tar -xf - -C "$GIT_EXPORT"

[ -d "$SOURCE_CANONICAL" ] || fail "CANONICAL_GIT_EXPORT_NOT_FOUND"
[ -f "$SOURCE_CANONICAL/MANIFEST_SHA256.json" ] || fail "CANONICAL_MANIFEST_NOT_FOUND"

if find "$SOURCE_CANONICAL" -type l -print -quit | grep -q .; then
  fail "CANONICAL_SOURCE_CONTAINS_SYMLINK"
fi

(
  cd "$SOURCE_CANONICAL"
  PYTHONPATH="$SOURCE_CANONICAL" python3 -B -m a4.verify --hashes-only
) | tee "$EVIDENCE/source-hashes.log"

printf 'SOURCE_WORKTREE=%s
SOURCE_CANONICAL=%s
'   "$SOURCE" "$SOURCE_CANONICAL" > "$EVIDENCE/source-selection.env"

IGNORED_COUNT="$(wc -l < "$EVIDENCE/source-ignored-files.txt" | tr -d ' ')"
echo "A4_SOURCE_IGNORED_ARTIFACTS=$IGNORED_COUNT"

if [ "$IGNORED_COUNT" -gt 0 ]; then
  echo "Artefatos ignorados preservados na working tree e excluidos do pacote canonico:"
  sed -n '1,40p' "$EVIDENCE/source-ignored-files.txt"
fi

# -------- Gate 2: clone isolado --------
echo "[2/8] Criando staging isolado FIX4 a partir do HEAD canonico"
mkdir -p "$WORK"
cp -a "$SOURCE_CANONICAL/." "$WORK/"

# Contrato FIX4: raydium_protocols.rs é restaurado byte-a-byte do A2.
cp --preserve=mode,timestamps \
  "$WORK/engine_a2/src/raydium_protocols.rs" \
  "$WORK/engine_a4/src/raydium_protocols.rs"

# Namespace/release FIX4: não sobrescreve versões anteriores.
python3 - "$WORK" <<'PY'
from pathlib import Path
import json, sys

root = Path(sys.argv[1])

p = root / "a4" / "install.py"
s = p.read_text()
old = "RELEASE='belial-a4-90m-v1-fix1'"
new = "RELEASE='belial-a4-90m-v1-fix4'"
if old not in s and new not in s:
    raise SystemExit("INSTALL_RELEASE_MARKER_NOT_FOUND")
p.write_text(s.replace(old, new))

p = root / "a4" / "common.py"
s = p.read_text()
old = "VERSION='BELIAL_A4_90M_V1'"
new = "VERSION='BELIAL_A4_90M_V1_FIX4'"
if old not in s and new not in s:
    raise SystemExit("COMMON_VERSION_MARKER_NOT_FOUND")
p.write_text(s.replace(old, new))

p = root / "docs" / "PRESERVATION_A4.json"
doc = json.loads(p.read_text())
changes = list(doc.get("intentional_changes") or [])
msg = "FIX4 restores engine_a4/src/raydium_protocols.rs byte-identical to engine_a2; native delta remains src/main.rs only"
if msg not in changes:
    changes.append(msg)
doc["intentional_changes"] = changes
doc["fix4"] = {
    "native_delta_expected": ["src/main.rs"],
    "raydium_protocols_restored_from": "engine_a2/src/raydium_protocols.rs",
    "strategy_thresholds_changed": False,
    "sizing_changed": False,
    "exit_policies_changed": False,
    "orders": False,
}
p.write_text(json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
PY

# -------- Gate 3: delta nativo congelado --------
echo "[3/8] Gate de delta nativo"

python3 - "$WORK" "$EVIDENCE/native-delta.json" <<'PY'
from pathlib import Path
import hashlib, json, sys

root = Path(sys.argv[1])
out = Path(sys.argv[2])
a = root / "engine_a2"
b = root / "engine_a4"

def digest(p):
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

left = {str(x.relative_to(a)) for x in a.rglob("*") if x.is_file()}
right = {str(x.relative_to(b)) for x in b.rglob("*") if x.is_file()}
if left != right:
    raise SystemExit("ENGINE_MEMBER_SET_CHANGED:" + repr(sorted(left ^ right)[:20]))

changed = [n for n in sorted(left) if digest(a/n) != digest(b/n)]
result = {
    "changed": changed,
    "expected": ["src/main.rs"],
    "raydium_identical": digest(a/"src/raydium_protocols.rs") == digest(b/"src/raydium_protocols.rs"),
    "orders": False,
}
out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
print("A4_FIX4_NATIVE_CHANGED=" + repr(changed))
print("A4_FIX4_RAYDIUM_IDENTICAL=" + str(result["raydium_identical"]).lower())

if changed != ["src/main.rs"]:
    raise SystemExit("FROZEN_NATIVE_DELTA_FAILED:" + repr(changed))
if not result["raydium_identical"]:
    raise SystemExit("RAYDIUM_NOT_IDENTICAL")
PY

# -------- Gate 4: recalcula manifesto selado --------
echo "[4/8] Recalculando manifesto do pacote FIX4"

python3 - "$WORK" <<'PY'
from pathlib import Path
import hashlib, json, sys

root = Path(sys.argv[1])
manifest = root / "MANIFEST_SHA256.json"
doc = json.loads(manifest.read_text())
if doc.get("schema") != "belial_a4_package_v1":
    raise SystemExit("UNEXPECTED_MANIFEST_SCHEMA")

files = {}
for p in sorted(root.rglob("*")):
    if not p.is_file():
        continue
    rel = str(p.relative_to(root))
    if rel == "MANIFEST_SHA256.json":
        continue
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    files[rel] = h.hexdigest()

doc["files"] = files
doc["fix4_native_delta"] = ["src/main.rs"]
doc["orders"] = False
manifest.write_text(json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
print("A4_FIX4_MANIFEST_FILES=" + str(len(files)))
PY

(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m a4.verify --hashes-only
) | tee "$EVIDENCE/fix4-hashes.log"

if [ "$MODE" = "--verificar-pacote" ]; then
  echo
  echo "BELIAL_A4_FIX4_PACKAGE_ONLY=PASS"
  echo "Nenhuma release, snapshot Git ou runtime foi alterado."
  exit 0
fi

# -------- Gate 5: TODAS as suítes offline --------
echo "[5/8] Suítes offline completas"
mkdir -p "$EVIDENCE/tests"

(
  cd "$WORK"
  PYTHONPATH="$WORK" python3 -B -m a4.verify --log-dir "$EVIDENCE/tests"
) | tee "$EVIDENCE/offline-suite.log"

python3 - "$EVIDENCE/tests/VERIFY_RESULT.json" <<'PY'
import json, sys
doc=json.load(open(sys.argv[1]))
if not doc.get("ok"):
    raise SystemExit("VERIFY_RESULT_NOT_OK")
a4=(doc.get("groups") or {}).get("a4") or {}
if not a4.get("passed"):
    raise SystemExit("A4_GROUP_NOT_GREEN")
print("A4_FIX4_OFFLINE_TOTAL_TESTS=" + str(doc.get("tests")))
print("A4_FIX4_A4_TESTS=" + str(a4.get("tests")))
print("A4_FIX4_A4_PASS=true")
PY

# -------- Gate 6: Rust nativo, offline, 1 job --------
echo "[6/8] Rust native test/build"
mkdir -p "$TARGET"
export CARGO_TARGET_DIR="$TARGET"
export CARGO_NET_OFFLINE=true
export TOKIO_WORKER_THREADS=1

for BIN in belial-perp-feed belial-spot-raw belial-r5c2-live-profiler; do
  echo "RUST_TEST=$BIN"
  cargo test \
    --release \
    --locked \
    --offline \
    --jobs 1 \
    --manifest-path "$WORK/engine_a4/Cargo.toml" \
    --bin "$BIN" \
    >"$EVIDENCE/rust-test-$BIN.log" 2>&1 || {
      tail -n 160 "$EVIDENCE/rust-test-$BIN.log" >&2
      fail "RUST_TEST_FAILED:$BIN"
    }

  echo "RUST_BUILD=$BIN"
  cargo build \
    --release \
    --locked \
    --offline \
    --jobs 1 \
    --manifest-path "$WORK/engine_a4/Cargo.toml" \
    --bin "$BIN" \
    >"$EVIDENCE/rust-build-$BIN.log" 2>&1 || {
      tail -n 160 "$EVIDENCE/rust-build-$BIN.log" >&2
      fail "RUST_BUILD_FAILED:$BIN"
    }
done

# Reconfirma delta após build (build nunca pode alterar source).
python3 - "$WORK" <<'PY'
from pathlib import Path
import hashlib, sys
root=Path(sys.argv[1])
a=root/"engine_a2"; b=root/"engine_a4"
def d(p): return hashlib.sha256(p.read_bytes()).hexdigest()
left={str(x.relative_to(a)) for x in a.rglob("*") if x.is_file()}
right={str(x.relative_to(b)) for x in b.rglob("*") if x.is_file()}
changed=[n for n in sorted(left) if d(a/n)!=d(b/n)]
if left!=right or changed!=["src/main.rs"]:
    raise SystemExit("POST_BUILD_NATIVE_DELTA_FAILED:"+repr(changed))
print("A4_FIX4_POST_BUILD_NATIVE_DELTA=PASS")
PY

# -------- Gate 7: instala release imutável --------
echo "[7/8] Instalando release imutável"

if [ -e "$DEST" ]; then
  if diff -qr -- "$WORK" "$DEST" >"$EVIDENCE/release-reuse.diff" 2>&1; then
    echo "A4_FIX4_RELEASE_REUSED_EXACT=true"
  else
    echo "Release existente difere do pacote FIX4." >&2
    cat "$EVIDENCE/release-reuse.diff" >&2
    fail "EXISTING_FIX4_RELEASE_DIFFERS"
  fi
else
  STAGING_RELEASE="$RELEASES/.belial-a4-fix4-staging-$TS"
  cp -a "$WORK" "$STAGING_RELEASE"
  (
    cd "$STAGING_RELEASE"
    PYTHONPATH="$STAGING_RELEASE" python3 -B -m a4.verify --hashes-only
  ) >"$EVIDENCE/installed-hash-check.log" 2>&1
  mv "$STAGING_RELEASE" "$DEST"
  echo "A4_FIX4_RELEASE_INSTALLED=$DEST"
fi

sha256sum \
  "$DEST/MANIFEST_SHA256.json" \
  "$DEST/engine_a4/src/main.rs" \
  "$DEST/engine_a4/src/raydium_protocols.rs" \
  > "$EVIDENCE/release-critical.sha256"

# -------- Gate 8: exporta estado versionável para Git --------
echo "[8/8] Preparando checkpoint Git"

if [ -e "$NEW_SNAPSHOT" ]; then
  if ! diff -qr -- "$WORK" "$NEW_SNAPSHOT" >"$EVIDENCE/git-snapshot-reuse.diff" 2>&1; then
    echo "Snapshot Git FIX4 existente difere do pacote validado." >&2
    cat "$EVIDENCE/git-snapshot-reuse.diff" >&2
    fail "EXISTING_GIT_FIX4_SNAPSHOT_DIFFERS"
  fi
  echo "A4_FIX4_GIT_SNAPSHOT_REUSED_EXACT=true"
else
  mkdir -p "$(dirname "$NEW_SNAPSHOT")"
  cp -a "$WORK" "$NEW_SNAPSHOT"
fi

mkdir -p "$(dirname "$INSTALLER_DEST")"
cp -- "$SELF" "$INSTALLER_DEST"
chmod 0755 "$INSTALLER_DEST"

PACKAGE_SHA="$(sha256sum "$DEST/MANIFEST_SHA256.json" | awk '{print $1}')"
INSTALLER_SHA="$(sha256sum "$INSTALLER_DEST" | awk '{print $1}')"
MAIN_SHA="$(sha256sum "$DEST/engine_a4/src/main.rs" | awk '{print $1}')"
RAY_SHA="$(sha256sum "$DEST/engine_a4/src/raydium_protocols.rs" | awk '{print $1}')"
A2_RAY_SHA="$(sha256sum "$DEST/engine_a2/src/raydium_protocols.rs" | awk '{print $1}')"

[ "$RAY_SHA" = "$A2_RAY_SHA" ] || fail "FINAL_RAYDIUM_HASH_MISMATCH"

# Handoff versionável. Não afirma captura de 90 min.
cat > "$GIT_REPO/docs/HANDOFF_A4_FIX4.md" <<EOF
# Bot Belial — Handoff A4 FIX4

Gerado por \`BELIAL_A4_90M_FIX4_ATUALIZAR.sh\` em **$TS**.

## Gate encerrado

- PAPER / SHADOW: **preservado**
- ordens reais: **desabilitadas**
- universo A4 congelado: **24 ativos**
- delta nativo permitido: **somente \`engine_a4/src/main.rs\`**
- \`engine_a4/src/raydium_protocols.rs\`: **byte-idêntico ao A2**
- suítes offline: **PASS**
- Rust native test/build: **PASS**
- release imutável: \`$DEST\`

## Hashes críticos

\`\`\`text
MANIFEST_SHA256.json  $PACKAGE_SHA
engine_a4/main.rs     $MAIN_SHA
raydium_protocols.rs  $RAY_SHA
installer             $INSTALLER_SHA
\`\`\`

## Próximo estágio

Não modificar o A4 para reduzir pools.

A redução nasce em linha isolada **BELIAL_A5_16Q**:

\`\`\`text
FARTCOIN
BOME
WIF
POPCAT
RAY
JUP
BONK
PENGU
PUMP
USELESS
GRASS
PYTH
RENDER
KMNO
MEW
MPLX
\`\`\`

Direção já aprovada para A5:

1. 16 pools, qualidade acima de quantidade;
2. terminal Bloomberg preservando F1 Mercado / F2 Posições / F3 Agente / F4 Fontes;
3. caminho visual em Rust;
4. last-good-price + freshness explícita, sem inventar preço;
5. N2 fast path sem JSON/filesystem no caminho quente;
6. sensores externos primeiro em SHADOW;
7. nenhuma promoção para execução real sem novo gate explícito.

## Runtime

Este checkpoint comprova fechamento de pacote/test/build local.
Ele **não** afirma que uma nova sessão de mercado de 90 minutos foi executada.
EOF

cat > "$GIT_REPO/docs/CURRENT_STATE.md" <<EOF
# Bot Belial — Estado atual

## Continuidade

Continuação direta do projeto **Belial 0.0.5**.

Raiz operacional local:

\`\`\`text
/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter
\`\`\`

## Segurança operacional

- PAPER / SHADOW somente.
- Nenhuma ordem financeira real nesta fase.
- Contratos/testes congelados não são relaxados para fazer instalação passar.
- Backups, hashes, instaladores e evidências locais são preservados.

## A4 FIX4

O A4 permanece com **24 ativos**.

O FIX4 fecha especificamente a regressão observada no FIX3:

\`\`\`text
esperado: ['src/main.rs']
FIX3:     ['src/main.rs', 'src/raydium_protocols.rs']
FIX4:     ['src/main.rs']
\`\`\`

\`engine_a4/src/raydium_protocols.rs\` é restaurado byte-a-byte de
\`engine_a2/src/raydium_protocols.rs\`.

Gates locais do pacote FIX4:

- suítes offline: PASS;
- grupo A4: PASS;
- Rust native test/build: PASS;
- release imutável: \`$RELEASE_NAME\`;
- ordens: false.

A captura de mercado de 90 minutos não é declarada por este checkpoint.

## Próxima linha

A próxima linha é **BELIAL_A5_16Q**, isolada do A4.

Universo:

\`\`\`text
FARTCOIN BOME WIF POPCAT
RAY JUP BONK PENGU
PUMP USELESS GRASS PYTH
RENDER KMNO MEW MPLX
\`\`\`

Direção:

- reduzir fan-out para 16 pools;
- melhorar freshness e estabilidade;
- impedir desaparecimento visual de preço sem inventar cotação;
- terminal Bloomberg F1/F2/F3/F4 com caminho visual em Rust;
- N2 fast path;
- sensores externos somente SHADOW inicialmente.
EOF

DEEP_LOG="$GIT_REPO/Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv"
if [ ! -f "$DEEP_LOG" ]; then
  printf 'utc\tphase\tstatus\trelease\tmanifest_sha256\tinstaller_sha256\n' > "$DEEP_LOG"
fi
if ! grep -Fq "$RELEASE_NAME" "$DEEP_LOG"; then
  printf '%s\tA4_FIX4\tLOCAL_GATES_PASS\t%s\t%s\t%s\n' \
    "$TS" "$RELEASE_NAME" "$PACKAGE_SHA" "$INSTALLER_SHA" >> "$DEEP_LOG"
fi

RELEASE_INDEX="$GIT_REPO/Belial Negro/Raydium Hunter/manifests/release-index.tsv"
if [ -f "$RELEASE_INDEX" ] && ! grep -Fq "$RELEASE_NAME" "$RELEASE_INDEX"; then
  printf '%s\tdir\t%s\n' "$RELEASE_NAME" "$(date -u +%Y-%m-%dT%H:%M:%S.0000000000)" >> "$RELEASE_INDEX"
fi

ORIGIN="$GIT_REPO/Belial Negro/Raydium Hunter/manifests/installer-origin.tsv"
if [ -f "$ORIGIN" ] && ! grep -Fq 'BELIAL_A4_90M_FIX4_ATUALIZAR.sh' "$ORIGIN"; then
  printf 'BELIAL_A4_90M_FIX4_ATUALIZAR.sh\t%s\n' "$SELF" >> "$ORIGIN"
fi

CHECKPOINT="$GIT_REPO/Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A4_FIX4.sha256"
(
  cd "$GIT_REPO"
  sha256sum \
    "$NEW_REL/MANIFEST_SHA256.json" \
    "$NEW_REL/engine_a4/src/main.rs" \
    "$NEW_REL/engine_a4/src/raydium_protocols.rs" \
    "$INSTALLER_REL" \
    docs/HANDOFF_A4_FIX4.md \
    docs/CURRENT_STATE.md \
    > "$CHECKPOINT"
)

cat > "$EVIDENCE/RESULT.env" <<EOF
BELIAL_A4_FIX4=PASS
UTC=$TS
RELEASE=$DEST
SOURCE_SNAPSHOT=$NEW_SNAPSHOT
PACKAGE_MANIFEST_SHA256=$PACKAGE_SHA
INSTALLER_SHA256=$INSTALLER_SHA
ENGINE_A4_MAIN_SHA256=$MAIN_SHA
RAYDIUM_SHA256=$RAY_SHA
ORDERS=false
PAPER=true
SHADOW=true
GIT_COMMIT_PERFORMED=false
EOF

cp "$EVIDENCE/RESULT.env" "$BACKUP/RESULT.env"

echo
echo "============================================================"
echo " BELIAL A4 FIX4 = PASS"
echo "============================================================"
cat "$EVIDENCE/RESULT.env"
echo
echo "Git foi PREPARADO, mas NÃO foi commitado automaticamente."
echo
echo "=== GIT STATUS ==="
(
  cd "$GIT_REPO"
  git status --short
)
echo
echo "=== COMANDOS PARA CHECKPOINT GIT ==="
cat <<EOF
cd "$GIT_REPO"

git add -- \
  "$NEW_REL" \
  "$INSTALLER_REL" \
  "Belial Negro/Raydium Hunter/manifests/deep-change-log.tsv" \
  "Belial Negro/Raydium Hunter/manifests/release-index.tsv" \
  "Belial Negro/Raydium Hunter/manifests/installer-origin.tsv" \
  "Belial Negro/Raydium Hunter/manifests/CHECKPOINT_A4_FIX4.sha256" \
  "docs/HANDOFF_A4_FIX4.md" \
  "docs/CURRENT_STATE.md"

git diff --cached --stat
git diff --cached --check

git commit -m "fix(a4): close FIX4 with frozen native delta"
git push origin main
EOF

echo
echo "Após o commit/push, envie a saída de:"
echo "git status --short && git log -1 --oneline --decorate"
echo
echo "Não inicie A5-16Q antes de confirmarmos este checkpoint Git."
