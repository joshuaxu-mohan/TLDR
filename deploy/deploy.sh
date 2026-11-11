#!/usr/bin/env bash
# Deploy TLDR on the Oracle VM. Run as the app user; sudo is used for systemctl.
#   bash deploy/deploy.sh [git-ref]    deploy a ref (default origin/main)
#   bash deploy/deploy.sh --rollback   restore code and frontend from the last deploy
# Steps: preflight, DB backup, fast-forward, pinned-deps check, frontend build
# with VITE_API_TOKEN read from .env, dist swap, API restart, verify.
# Never prints secrets. Overrides: APP_DIR, UNIT (systemd unit), PY (venv python).
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0

APP_DIR="${APP_DIR:-$HOME/Daily}"
STATE_DIR="$HOME/deploys"
BACKUP_DIR="$HOME/backups"
DIST_BACKUPS="$HOME/dist-backups"
API="http://127.0.0.1:8000"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
ARMED=0
PREV=""

say()  { printf '\n== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "$ARMED" = 1 ]; then
    echo "!! failed before the restart: code reset to $PREV, service untouched" >&2
    git -C "$APP_DIR" reset -q --hard "$PREV" || true
    rm -rf "$APP_DIR/frontend/dist.new"
  fi
}
trap on_exit EXIT

# Print one .env value. Callers capture it; it is never echoed.
envval() {
  python3 - "$APP_DIR/.env" "$1" <<'PY'
import re, sys
val = ""
for line in open(sys.argv[1], encoding="utf-8"):
    m = re.match(r"\s*(?:export\s+)?([A-Za-z_]\w*)\s*=\s*(.*?)\s*$", line)
    if m and m.group(1).lower() == sys.argv[2].lower():
        val = m.group(2)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        else:
            val = re.sub(r"\s+#.*$", "", val)
print(val)
PY
}

detect() {
  if [ -z "${UNIT:-}" ]; then
    local found=()
    mapfile -t found < <(grep -lsE 'src\.delivery\.api' /etc/systemd/system/*.service || true)
    [ "${#found[@]}" -eq 1 ] || fail "found ${#found[@]} units running src.delivery.api; set UNIT=<name>"
    UNIT="$(basename "${found[0]}")"
  fi
  if [ -z "${PY:-}" ]; then
    local exe c
    exe="$(systemctl show -p ExecStart --value "$UNIT" | sed -n 's/.*path=\([^ ;]*\).*/\1/p' | head -n1)"
    for c in "$(dirname "${exe:-/nonexistent}")/python" "$(dirname "${exe:-/nonexistent}")/python3"; do
      [ -x "$c" ] && PY="$c" && break
    done
  fi
  [ -n "${PY:-}" ] || fail "cannot find the unit's python; set PY=/path/to/venv/bin/python"
}

verify() {
  say "Verify"
  local bad=0 j='Content-Type: application/json' s js since="$1"
  st() { curl -s -o /dev/null -w '%{http_code}' "$@" || true; }
  ok() {
    if [ "$2" = "$3" ]; then echo "PASS  $1 ($2)"; else echo "FAIL  $1 (got $2, want $3)"; bad=1; fi
  }
  ok "unit active"                 "$(systemctl is-active "$UNIT" || true)" active
  ok "GET /api/sources stays open" "$(st "$API/api/sources")" 200
  ok "PATCH source, no token"      "$(st -X PATCH -H "$j" -d '{}' "$API/api/sources/999999")" 401
  ok "DELETE source, no token"     "$(st -X DELETE "$API/api/sources/999999")" 401
  ok "POST source, no token"       "$(st -X POST -H "$j" -d '{}' "$API/api/sources")" 401
  ok "POST validate-url, no token" "$(st -X POST -H "$j" -d '{"url":""}' "$API/api/sources/validate-url")" 401
  ok "POST discover, no token"     "$(st -X POST -H "$j" -d '{"type":"x","query":"x"}' "$API/api/sources/discover")" 401
  ok "PATCH source, with token (404 = reached handler)" \
     "$(printf 'Authorization: Bearer %s\n' "$TOKEN" | st -H @- -X PATCH -H "$j" -d '{}' "$API/api/sources/999999")" 404
  s="$(st -X POST -d 'From=x&Body=y' "$API/api/whatsapp/webhook")"
  ok "webhook skips bearer (got $s; 403 = its Twilio check)" "$([ "$s" != 401 ] && echo exempt || echo blocked)" exempt
  js="$(curl -s "$API/" | grep -oE '/assets/index-[A-Za-z0-9_-]+\.js' | head -n1 || true)"
  ok "index.html points at the new bundle" "$([ -n "$js" ] && [ -f "frontend/dist$js" ] && echo yes || echo no)" yes
  curl -s -o "$STATE_DIR/served-bundle.js" "$API${js:-/missing}" || true
  ok "served bundle carries the token" \
     "$(grep -qF -f <(printf '%s\n' "$TOKEN") "$STATE_DIR/served-bundle.js" && echo yes || echo no)" yes
  rm -f "$STATE_DIR/served-bundle.js"
  ok "no missing-token error since restart" \
     "$(sudo journalctl -u "$UNIT" --since "$since" --no-pager 2>/dev/null | grep -c 'API_BEARER_TOKEN is not set' || true)" 0
  echo "info  openapi paths: $(curl -s "$API/openapi.json" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["paths"]))' 2>/dev/null || echo '?')"
  [ "$bad" = 0 ] || fail "verification failed; to undo: bash $0 --rollback"
  say "DEPLOY OK at $(git rev-parse --short HEAD)"
}

deploy() {
  local ref="$1" db bk out restart_at
  cd "$APP_DIR"

  say "Preflight"
  detect
  echo "unit=$UNIT python=$PY ref=$ref"
  [ -z "$(git status --porcelain --untracked-files=no)" ] || fail "tracked files are modified; reconcile first"
  ! pgrep -af 'src\.main.*--(run-now|ingest-only|backfill)' >/dev/null || fail "a pipeline run is in progress; retry after it ends"
  command -v npm >/dev/null || fail "npm not found (the Vite build needs Node >= 18)"
  node -e 'process.exit(+process.versions.node.split(".")[0] >= 18 ? 0 : 1)' || fail "Node >= 18 needed, found $(node -v)"
  TOKEN="$(envval API_BEARER_TOKEN)"
  [ "${#TOKEN}" -ge 16 ] || fail "API_BEARER_TOKEN in .env is missing or shorter than 16 characters"
  db="$(systemctl show -p Environment --value "$UNIT" | tr ' ' '\n' | sed -n 's/^DB_PATH=//p' || true)"
  db="${db:-$(envval DB_PATH)}"
  db="${db:-data/digest.db}"
  [ -f "$db" ] || fail "database not found at $db"

  say "DB backup"
  mkdir -p "$BACKUP_DIR" "$STATE_DIR" "$DIST_BACKUPS"
  bk="$BACKUP_DIR/digest-$TS.db"
  python3 - "$db" "$bk" <<'PY'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)
assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "backup failed integrity_check"
n = {t: dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("sources", "articles")}
print(f"{sys.argv[1]} -> {sys.argv[2]}  sources={n['sources']} articles={n['articles']}")
PY
  sha256sum "$bk"

  say "Code"
  PREV="$(git rev-parse HEAD)"
  git fetch -q origin
  git merge -q --ff-only "$ref" || fail "cannot fast-forward to $ref"
  ARMED=1
  printf 'PREV=%s\nNEW=%s\nUNIT=%s\nDB=%s\nBACKUP=%s\nDIST=%s\n' \
    "$PREV" "$(git rev-parse HEAD)" "$UNIT" "$db" "$bk" "$DIST_BACKUPS/dist-$TS" > "$STATE_DIR/last-deploy.env"
  echo "$(git rev-parse --short "$PREV") -> $(git rev-parse --short HEAD)"

  say "Pinned Python deps"
  out="$("$PY" -m pip install --dry-run -r requirements.txt 2>&1)" || { printf '%s\n' "$out" | tail -n 5; fail "pip dry-run failed"; }
  if grep -E '^Would install' <<<"$out"; then fail "venv differs from requirements.txt (above); install deliberately, then rerun"; fi
  echo "venv matches requirements.txt"

  say "Frontend build"
  ( cd frontend
    npm ci --no-audit --no-fund --loglevel=error
    rm -rf dist.new
    VITE_API_TOKEN="$TOKEN" VITE_API_URL=/api npm run build --silent -- --outDir dist.new --emptyOutDir --logLevel warn )
  grep -qF -f <(printf '%s\n' "$TOKEN") frontend/dist.new/assets/*.js || fail "new bundle does not contain the token"
  echo "built: $(cd frontend/dist.new/assets && ls ./*.js | tr '\n' ' ')"

  say "Swap dist, restart $UNIT"
  if [ -d frontend/dist ]; then mv frontend/dist "$DIST_BACKUPS/dist-$TS"; fi
  mv frontend/dist.new frontend/dist
  ARMED=0
  restart_at="$(date '+%Y-%m-%d %H:%M:%S')"
  sudo systemctl restart "$UNIT"
  for _ in $(seq 30); do curl -fsS -o /dev/null "$API/openapi.json" 2>/dev/null && break; sleep 1; done
  verify "$restart_at"
}

rollback() {
  cd "$APP_DIR"
  [ -f "$STATE_DIR/last-deploy.env" ] || fail "no $STATE_DIR/last-deploy.env"
  # shellcheck disable=SC1091
  . "$STATE_DIR/last-deploy.env"
  say "Rollback to $PREV"
  git reset -q --hard "$PREV"
  if [ -d "$DIST" ]; then rm -rf frontend/dist; mv "$DIST" frontend/dist; fi
  sudo systemctl restart "$UNIT"
  echo "code=$(git rev-parse --short HEAD); frontend restored; DB untouched (backup: $BACKUP)"
}

case "${1:-}" in
  --rollback) rollback ;;
  -h|--help)  sed -n '2,7p' "$0" ;;
  *)          deploy "${1:-origin/main}" ;;
esac
