#!/usr/bin/env bash
# Sauvegarde du centre local (Mac) vers le NAS, par Tailscale.
#
# Le centre tourne sur ce Mac (édition « centre ») : base SQLite
# backend/mathprint.db + dossier data/ (copies, scans, overlays, secrets).
# Ce script envoie sur le NAS :
#   • un instantané COHÉRENT de la base (API de sauvegarde SQLite, sûre pendant
#     que l'application tourne), compressé :
#       db/horaire/mathprint-centre-AAAAMMJJ-HHMM.db.gz   (48 derniers gardés)
#       db/quotidien/mathprint-centre-AAAAMMJJ.db.gz      (90 derniers gardés)
#   • une copie de data/ (sans les caches régénérables) : data/
#     Envoi incrémental (fichiers modifiés depuis la dernière sauvegarde
#     réussie), par tar sur SSH : le NAS refuse rsync tant que le service rsync
#     de DSM est désactivé. Un fichier supprimé sur le Mac reste sur le NAS.
#   • data/runtime_secrets.env (clés de signature des DataMatrix et des
#     sessions) : sans lui, une base restaurée ne reconnaît plus ses copies.
#
# Lancé toutes les heures par launchd (scripts/launchd/com.kironina.backup-nas.plist).
# Journal : ~/Library/Logs/kironina-backup-nas.log
#
# Usage : scripts/backup-nas.sh            (sauvegarde)
#         scripts/backup-nas.sh --dry-run  (liste ce qui serait envoyé, n'envoie rien)
#         scripts/backup-nas.sh --full     (renvoie tout data/, pas seulement les nouveautés)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAS_HOST="${NAS_HOST:-Etienne@fabre.tail428d1a.ts.net}"
# dans le dossier personnel du compte sur le NAS (hors du dossier Docker de prod)
NAS_DIR="${NAS_BACKUP_DIR:-sauvegardes/kironina-centre}"
DB="$ROOT/backend/mathprint.db"
DATA="$ROOT/data"
KEEP_HOURLY=48
KEEP_DAILY=90
SSH=(ssh -o ConnectTimeout=15 -o BatchMode=yes -o ServerAliveInterval=30)
# date de la dernière sauvegarde réussie de data/ (fichier témoin)
STATE="${HOME}/.kironina-backup-nas.last"

DRY=""
case "${1:-}" in
  --dry-run) DRY=1 ;;
  --full) rm -f "$STATE" ;;
esac

log() { printf '%s [sauvegarde] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { log "ÉCHEC : $*"; exit 1; }

[ -f "$DB" ] || die "base introuvable : $DB"

# Une seule sauvegarde à la fois (un premier envoi complet peut dépasser l'heure).
LOCK="${TMPDIR:-/tmp}/kironina-backup-nas.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  log "une sauvegarde est déjà en cours ($LOCK) : rien à faire"
  exit 0
fi
WORK="$(mktemp -d)"
trap 'rm -rf "$LOCK" "$WORK"' EXIT

# NAS joignable ? (Tailscale arrêté, NAS en veille ou surchargé : on réessaiera
# à la prochaine heure)
"${SSH[@]}" "$NAS_HOST" true 2>/dev/null || die "NAS injoignable ($NAS_HOST) : Tailscale actif ?"

stamp="$(date '+%Y%m%d-%H%M')"
day="$(date '+%Y%m%d')"
snap="$WORK/mathprint-centre-$stamp.db"

# 1. instantané cohérent de la base, vérifié avant envoi
/usr/bin/sqlite3 "$DB" ".timeout 30000" ".backup '$snap'"
check="$(/usr/bin/sqlite3 "$snap" 'PRAGMA integrity_check;' | head -1)"
[ "$check" = "ok" ] || die "instantané corrompu : $check"
gzip -9 "$snap"
log "base : $(du -h "$snap.gz" | cut -f1) compressée"

"${SSH[@]}" "$NAS_HOST" "mkdir -p '$NAS_DIR/db/horaire' '$NAS_DIR/db/quotidien' '$NAS_DIR/data'"

if [ -z "$DRY" ]; then
  name="$(basename "$snap").gz"
  "${SSH[@]}" "$NAS_HOST" "cat > '$NAS_DIR/db/horaire/$name.part' && mv '$NAS_DIR/db/horaire/$name.part' '$NAS_DIR/db/horaire/$name'" < "$snap.gz"
  # instantané du jour : remplacé à chaque passage, il garde le dernier état de la journée
  "${SSH[@]}" "$NAS_HOST" "cp -f '$NAS_DIR/db/horaire/$name' '$NAS_DIR/db/quotidien/mathprint-centre-$day.db.gz'"
fi

# 2. data/ : fichiers nouveaux ou modifiés depuis la dernière sauvegarde réussie
#    (les caches se régénèrent : inutile de les envoyer)
marker="$WORK/marker"; touch "$marker"     # posé AVANT la liste : rien n'échappe
newer=(); [ -f "$STATE" ] && newer=(-newer "$STATE")
( cd "$DATA" && find . \( -path ./mathcache -o -path ./figcache -o -path ./cardcache \
      -o -path ./tmp -o -path ./scans/sandbox_tmp \) -prune \
    -o -type f ! -name .DS_Store ${newer[@]+"${newer[@]}"} -print ) > "$WORK/list"
count="$(wc -l < "$WORK/list" | tr -d ' ')"
if [ -n "$DRY" ]; then
  log "data/ : $count fichier(s) à envoyer (simulation)"; head -20 "$WORK/list"
elif [ "$count" -gt 0 ]; then
  ( cd "$DATA" && COPYFILE_DISABLE=1 tar --no-mac-metadata --no-xattrs --no-acls --no-fflags -czf - -T "$WORK/list" ) \
    | "${SSH[@]}" "$NAS_HOST" "tar -xzf - -C '$NAS_DIR/data'"
  log "data/ : $count fichier(s) envoyé(s)"
else
  log "data/ : rien de nouveau"
fi
[ -z "$DRY" ] && mv -f "$marker" "$STATE"

# 3. rotation sur le NAS
if [ -z "$DRY" ]; then
  "${SSH[@]}" "$NAS_HOST" "
    cd '$NAS_DIR/db/horaire' && ls -1 mathprint-centre-*.db.gz | sort | head -n -$KEEP_HOURLY | xargs -r rm -f
    cd '../quotidien' && ls -1 mathprint-centre-*.db.gz | sort | head -n -$KEEP_DAILY | xargs -r rm -f
    true"
fi

log "terminé → $NAS_HOST:$NAS_DIR"
