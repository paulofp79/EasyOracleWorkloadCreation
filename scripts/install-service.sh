#!/usr/bin/env bash
# Install the systemd service (run as root after scripts/install.sh was run as the app user).
#
#   sudo scripts/install-service.sh [--user oracle] [--host 127.0.0.1|0.0.0.0] [--port 8080]
#                                   [--no-start] [--dry-run]
#
# Writes /etc/systemd/system/pdb-orchestrator.service (always) and
# /etc/pdb-orchestrator/orchestrator.env (only if it does not exist yet: your edits are kept).
# PATH, ORACLE_HOME, ORACLE_SID and LD_LIBRARY_PATH are taken from the user's login shell.
# --dry-run prints both files instead of installing anything.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$PWD"
RUN_USER="$(stat -c %U "$APP_DIR")"
HOST=127.0.0.1
PORT=8080
START=true
DRY=false
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
ENV_DIR="${ENV_DIR:-/etc/pdb-orchestrator}"

while (( $# )); do
  case "$1" in
    --user) RUN_USER="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --no-start) START=false; shift ;;
    --dry-run) DRY=true; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option $1 (try --help)" >&2; exit 2 ;;
  esac
done

[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || { echo "Invalid port $PORT" >&2; exit 2; }
[[ "$HOST" =~ ^[0-9A-Fa-f.:]+$|^localhost$ ]] || { echo "Invalid host $HOST" >&2; exit 2; }
id "$RUN_USER" >/dev/null 2>&1 || { echo "No such user: $RUN_USER" >&2; exit 2; }
[[ "$RUN_USER" != root ]] || { echo "Refusing to run the service as root; use --user oracle" >&2; exit 2; }
$DRY || [[ $EUID -eq 0 ]] || { echo "Run as root (or use --dry-run)." >&2; exit 2; }
[[ -x "$APP_DIR/venv/bin/python" ]] || { echo "$APP_DIR/venv missing: run scripts/install.sh as $RUN_USER first" >&2; exit 2; }
GROUP="$(id -gn "$RUN_USER")"

# The user's login environment (oraenv settings in .bash_profile end up here).
if [[ "$(id -un)" == "$RUN_USER" ]]; then
  login_env() { bash -lc "$1"; }
else
  login_env() { su - "$RUN_USER" -c "$1"; }
fi
# Values are printed between markers so banners or messages from login scripts cannot leak in.
read_var() {
  local out
  out="$(login_env "printf 'ORCHVAL<<%s>>ORCHEND' \"\${$1:-}\"" 2>/dev/null || true)"
  [[ "$out" == *"ORCHVAL<<"*">>ORCHEND"* ]] || return 0
  out="${out#*ORCHVAL<<}"
  printf '%s' "${out%%>>ORCHEND*}"
}
U_PATH="$(read_var PATH)"; U_PATH="${U_PATH:-/usr/local/bin:/usr/bin:/bin}"
U_LD="$(read_var LD_LIBRARY_PATH)"
U_OH="$(read_var ORACLE_HOME)"
U_SID="$(read_var ORACLE_SID)"
line() { [[ -n "$2" ]] && printf '%s=%s' "$1" "$2" || printf '#%s=' "$1"; }

# Fill the templates with bash string replacement (no sed: values may contain any character).
shopt -u patsub_replacement 2>/dev/null || true      # bash >= 5.2: keep '&' in values literal
fill() {
  local text key
  text="$(cat "$1")"; shift
  while (( $# )); do key="$1"; text="${text//"$key"/"$2"}"; shift 2; done
  printf '%s' "$text"
}
unit="$(fill deploy/pdb-orchestrator.service.in @APP_DIR@ "$APP_DIR" @USER@ "$RUN_USER" @GROUP@ "$GROUP" \
          @ENV_FILE@ "$ENV_DIR/orchestrator.env")"
envf="$(fill deploy/orchestrator.env.in @HOST@ "$HOST" @PORT@ "$PORT" @USER@ "$RUN_USER" @PATH@ "$U_PATH" \
          @LD_LIBRARY_PATH@ "$(line LD_LIBRARY_PATH "$U_LD")" @ORACLE_HOME@ "$(line ORACLE_HOME "$U_OH")" \
          @ORACLE_SID@ "$(line ORACLE_SID "$U_SID")")"

if $DRY; then
  printf '### %s/pdb-orchestrator.service\n%s\n\n### %s/orchestrator.env\n%s\n' "$UNIT_DIR" "$unit" "$ENV_DIR" "$envf"
  exit 0
fi

install -d -m 755 "$UNIT_DIR"
printf '%s\n' "$unit" > "$UNIT_DIR/pdb-orchestrator.service"
chmod 644 "$UNIT_DIR/pdb-orchestrator.service"
install -d -m 750 -g "$GROUP" "$ENV_DIR"
if [[ -e "$ENV_DIR/orchestrator.env" ]]; then
  echo "Keeping the existing $ENV_DIR/orchestrator.env (compare with: $0 --dry-run)"
else
  printf '%s\n' "$envf" > "$ENV_DIR/orchestrator.env"
  chown "root:$GROUP" "$ENV_DIR/orchestrator.env"
  chmod 640 "$ENV_DIR/orchestrator.env"
fi
systemctl daemon-reload

host_now="$(sed -n 's/^ORCH_HOST=//p' "$ENV_DIR/orchestrator.env" | tail -1)"
if [[ -n "$host_now" && "$host_now" != 127.* && "$host_now" != localhost && "$host_now" != ::1 ]]; then
  logins="$(cd "$APP_DIR/backend" && runuser -u "$RUN_USER" -- ../venv/bin/python -m app users 2>/dev/null || true)"
  if [[ -z "$logins" || "$logins" == "(no logins)" ]]; then
    echo "ORCH_HOST=$host_now needs a login first. As $RUN_USER run:"
    echo "  cd $APP_DIR/backend && ../venv/bin/python -m app set-password"
    START=false
  fi
fi
if $START; then
  systemctl enable --now pdb-orchestrator
  sleep 2
  systemctl --no-pager --lines=5 status pdb-orchestrator || true
else
  systemctl enable pdb-orchestrator
  echo "Enabled, not started. Start with: systemctl start pdb-orchestrator"
fi
echo "Logs: journalctl -u pdb-orchestrator -f"
