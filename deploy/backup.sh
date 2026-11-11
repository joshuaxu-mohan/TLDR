#!/usr/bin/env bash
# Nightly SQLite backup for the Oracle VM (run from cron; see deploy/crontab.txt).
# Uses SQLite's online backup API, so it is consistent while the pipeline or API
# is writing. Verifies the copy, gzips it, and keeps KEEP_DAYS days of nightly
# copies. Deploy-time backups (uncompressed digest-*.db from deploy.sh) are left alone.
# Overrides: APP_DIR, DB_PATH (relative to APP_DIR), BACKUP_DIR, KEEP_DAYS.
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/Daily}"
DB="${DB_PATH:-data/digest.db}"
DEST="${BACKUP_DIR:-$HOME/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"

cd "$APP_DIR"
[ -f "$DB" ] || { echo "$(date -u +%FT%TZ) backup FAILED: no database at $APP_DIR/$DB" >&2; exit 1; }
mkdir -p "$DEST"
out="$DEST/nightly-$(date -u +%Y%m%dT%H%M%SZ).db"

articles="$(python3 - "$DB" "$out" <<'PY'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
n = dst.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
dst.close()
src.close()
if ok != "ok":
    sys.exit(f"integrity_check returned {ok!r}")
print(n)
PY
)"
gzip -9f "$out"
find "$DEST" -maxdepth 1 -name 'nightly-*.db.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date -u +%FT%TZ) backup ok: $out.gz ($(du -h "$out.gz" | cut -f1), $articles articles), $(find "$DEST" -maxdepth 1 -name 'nightly-*.db.gz' | wc -l) nightly copies kept"
