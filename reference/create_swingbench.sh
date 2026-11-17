#!/usr/bin/env bash
#
# create_swingbench.sh - load the Swingbench SOE schema into a range of PDBs.
# Run with --help for all options.
set -Eeuo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
  echo "bash 4 or newer is required." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Defaults. Environment variables override these; command-line flags override both.
# Keep FIRST/LAST aligned with create_pdbs.sh.
# ---------------------------------------------------------------------------
FIRST="${FIRST:-222}"
LAST="${LAST:-250}"
PDB_PREFIX="${PDB_PREFIX:-PDBPP}"
SCAN_HOST="${SCAN_HOST:-scaqaq01-scan1.us.oracle.com}"
SCAN_PORT="${SCAN_PORT:-1521}"
PDB_SERVICE_SUFFIX="${PDB_SERVICE_SUFFIX:-}"
OEWIZARD="${OEWIZARD:-./oewizard}"
LOG_DIR="${LOG_DIR:-./swingbench-load-logs}"
SCALE="${SCALE:-0.1}"
THREADS="${THREADS:-16}"
TABLESPACE="${TABLESPACE:-SOETBS}"
PARALLEL="${PARALLEL:-1}"
KEEP_GOING="${KEEP_GOING:-false}"
FORCE="${FORCE:-false}"
DRY_RUN=false

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: ${0##*/} [options]

Loads the Swingbench SOE schema (via oewizard) into the PDBs
<PREFIX>_<FIRST> .. <PREFIX>_<LAST>, e.g. ${PDB_PREFIX}_$(printf '%03d' "$FIRST") .. ${PDB_PREFIX}_$(printf '%03d' "$LAST").
Run it as the OS user that can do "sqlplus / as sysdba" on the CDB.

PDB range
  -f, --first N            first PDB number                      [FIRST=${FIRST}]
  -l, --last N             last PDB number (max 999)             [LAST=${LAST}]
  -p, --prefix NAME        PDB name prefix                       [PDB_PREFIX=${PDB_PREFIX}]

Connection
      --scan-host HOST     SCAN (or host) used by oewizard       [SCAN_HOST=${SCAN_HOST}]
      --scan-port PORT     listener port                         [SCAN_PORT=${SCAN_PORT}]
      --service-suffix S   appended to the lower-case PDB name to
                           build the service, e.g. ".us.oracle.com"
                                                                 [PDB_SERVICE_SUFFIX="${PDB_SERVICE_SUFFIX}"]

Swingbench
  -s, --scale X            SOE scale factor, e.g. 0.1            [SCALE=${SCALE}]
  -t, --threads N          oewizard threads per PDB (-tc)        [THREADS=${THREADS}]
  -T, --tablespace NAME    tablespace for the SOE schema (-ts)   [TABLESPACE=${TABLESPACE}]
  -o, --oewizard PATH      path to the oewizard executable       [OEWIZARD=${OEWIZARD}]

Execution
  -n, --parallel N         PDBs loaded concurrently              [PARALLEL=${PARALLEL}]
  -L, --log-dir DIR        logs, status and "done" markers       [LOG_DIR=${LOG_DIR}]
      --keep-going         do not stop starting new loads after a failure
                           (default: finish running loads, then stop)
      --force              reload PDBs even if already marked as loaded
      --dry-run            check the PDBs and show the plan; load nothing
  -h, -help, --help        show this help

Passwords are never taken from the command line (they would show up in "ps").
Set SOE_PASSWORD and DBA_PASSWORD (SYS) in the environment, or you will be prompted.

Behaviour
  * Before loading anything, all PDBs in the range are checked: each must exist
    and be open READ WRITE, otherwise the script stops and lists the problem PDBs.
  * A PDB loaded successfully is marked in LOG_DIR/<PDB>.done and skipped on the
    next run (the marker is tied to the PDB's CON_UID, so a dropped and recreated
    PDB is loaded again). Re-run the same command to retry only the failed PDBs.
  * Ctrl-C stops all running loads, including their oewizard processes.
  * Exit status is 0 only if every selected PDB was loaded or already done.

Examples
  ${0##*/} --dry-run
  ${0##*/} --first 1 --last 50 --parallel 4
  ${0##*/} -f 222 -l 250 -n 8 -t 8 --scale 1 --keep-going
  SOE_PASSWORD=... DBA_PASSWORD=... ${0##*/} --prefix PDBPP --service-suffix .us.oracle.com
EOF
}

need_value() {
  [[ $# -ge 2 && -n "$2" ]] || die "Option $1 requires a value (try --help)."
}

while (( $# )); do
  case "$1" in
    -h|-help|--help)    usage; exit 0 ;;
    --*=*)              set -- "${1%%=*}" "${1#*=}" "${@:2}"; continue ;;
    -f|--first)         need_value "$@"; FIRST="$2";              shift 2 ;;
    -l|--last)          need_value "$@"; LAST="$2";               shift 2 ;;
    -p|--prefix)        need_value "$@"; PDB_PREFIX="$2";         shift 2 ;;
    --scan-host)        need_value "$@"; SCAN_HOST="$2";          shift 2 ;;
    --scan-port)        need_value "$@"; SCAN_PORT="$2";          shift 2 ;;
    --service-suffix)   need_value "$@"; PDB_SERVICE_SUFFIX="$2"; shift 2 ;;
    -s|--scale)         need_value "$@"; SCALE="$2";              shift 2 ;;
    -t|--threads)       need_value "$@"; THREADS="$2";            shift 2 ;;
    -T|--tablespace)    need_value "$@"; TABLESPACE="$2";         shift 2 ;;
    -o|--oewizard)      need_value "$@"; OEWIZARD="$2";           shift 2 ;;
    -n|--parallel)      need_value "$@"; PARALLEL="$2";           shift 2 ;;
    -L|--log-dir)       need_value "$@"; LOG_DIR="$2";            shift 2 ;;
    --keep-going)       KEEP_GOING=true;                          shift ;;
    --force)            FORCE=true;                               shift ;;
    --dry-run)          DRY_RUN=true;                             shift ;;
    *)                  die "Unknown option: $1 (try --help)." ;;
  esac
done

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
PDB_PREFIX="${PDB_PREFIX^^}"

[[ "$PDB_PREFIX" =~ ^[A-Za-z][A-Za-z0-9_]*$ ]] && (( ${#PDB_PREFIX} <= 26 )) \
  || die "Invalid prefix: must start with a letter, use only letters/digits/underscore, max 26 characters."
[[ "$FIRST" =~ ^[1-9][0-9]*$ && "$LAST" =~ ^[1-9][0-9]*$ ]] \
  || die "--first and --last must be positive integers without leading zeros."
(( FIRST <= LAST && LAST <= 999 )) \
  || die "Require 1 <= first <= last <= 999."
[[ "$PARALLEL" =~ ^[1-9][0-9]*$ ]] \
  || die "--parallel must be a positive integer."
[[ "$THREADS" =~ ^[1-9][0-9]*$ ]] \
  || die "--threads must be a positive integer."
[[ "$SCALE" =~ ^([0-9]+(\.[0-9]+)?|\.[0-9]+)$ && "$SCALE" =~ [1-9] ]] \
  || die "--scale must be a positive number, e.g. 0.1 or 5."
[[ "$SCAN_PORT" =~ ^[0-9]+$ ]] && (( SCAN_PORT >= 1 && SCAN_PORT <= 65535 )) \
  || die "--scan-port must be a number between 1 and 65535."
[[ "$SCAN_HOST" =~ ^[A-Za-z0-9._-]+$ ]] \
  || die "--scan-host contains invalid characters."
[[ "$PDB_SERVICE_SUFFIX" =~ ^[A-Za-z0-9._-]*$ ]] \
  || die "--service-suffix contains invalid characters."
[[ "$TABLESPACE" =~ ^[A-Za-z][A-Za-z0-9_\$#]*$ ]] \
  || die "--tablespace is not a valid tablespace name."
[[ "$KEEP_GOING" == true || "$KEEP_GOING" == false ]] \
  || die "KEEP_GOING must be true or false."
[[ "$FORCE" == true || "$FORCE" == false ]] \
  || die "FORCE must be true or false."

command -v sqlplus >/dev/null || die "sqlplus not found in PATH."
[[ -x "$OEWIZARD" ]] || die "oewizard is not executable: $OEWIZARD"

STATUS_DIR="${LOG_DIR}/.status"

# ---------------------------------------------------------------------------
# Pre-flight: every PDB in the range must exist and be open READ WRITE.
# ---------------------------------------------------------------------------
echo "Checking PDBs ${PDB_PREFIX}_$(printf '%03d' "$FIRST") through ${PDB_PREFIX}_$(printf '%03d' "$LAST")..."

if ! preflight_raw="$(sqlplus -L -s / as sysdba <<SQL
WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET DEFINE OFF
SET PAGESIZE 0 FEEDBACK OFF HEADING OFF VERIFY OFF ECHO OFF
SET LINESIZE 200 TRIMSPOOL ON

SELECT name || '|' || con_uid || '|' || SUM(CASE WHEN open_mode = 'READ WRITE' THEN 1 ELSE 0 END)
FROM   gv\$pdbs
WHERE  name LIKE '${PDB_PREFIX}%'
GROUP  BY name, con_uid
ORDER  BY name;

EXIT
SQL
)"; then
  echo "Could not check the PDBs. SQL*Plus output:" >&2
  printf '%s\n' "$preflight_raw" >&2
  exit 1
fi

declare -A pdb_uid=()
declare -A pdb_rw=()
while IFS='|' read -r n uid rw; do
  n="${n//[[:space:]]/}"; uid="${uid//[[:space:]]/}"; rw="${rw//[[:space:]]/}"
  [[ -n "$n" && -n "$uid" && "$rw" =~ ^[0-9]+$ ]] || continue
  pdb_uid["$n"]="$uid"
  pdb_rw["$n"]="$rw"
done <<< "$preflight_raw"

declare -a targets=()
declare -a missing=()
declare -a not_open=()
declare -a already_done=()

for ((i = FIRST; i <= LAST; i++)); do
  printf -v suffix '%03d' "$i"
  pdb="${PDB_PREFIX}_${suffix}"

  if [[ -z "${pdb_uid[$pdb]:-}" ]]; then
    missing+=("$pdb")
  elif (( pdb_rw[$pdb] < 1 )); then
    not_open+=("$pdb")
  elif [[ "$FORCE" != true && -f "${LOG_DIR}/${pdb}.done" \
          && "$(<"${LOG_DIR}/${pdb}.done")" == "${pdb_uid[$pdb]}" ]]; then
    already_done+=("$pdb")
  else
    targets+=("$i")
  fi
done

if (( ${#missing[@]} > 0 || ${#not_open[@]} > 0 )); then
  if (( ${#missing[@]} > 0 )); then
    echo "PDBs that do not exist (${#missing[@]}): ${missing[*]:0:10}$( (( ${#missing[@]} > 10 )) && echo ' ...')" >&2
  fi
  if (( ${#not_open[@]} > 0 )); then
    echo "PDBs not open READ WRITE (${#not_open[@]}): ${not_open[*]:0:10}$( (( ${#not_open[@]} > 10 )) && echo ' ...')" >&2
  fi
  die "Fix the PDBs above (or adjust --first/--last) and run again. Nothing was loaded."
fi

echo "PDBs to load: ${#targets[@]}; already loaded and skipped: ${#already_done[@]}."

if (( ${#targets[@]} == 0 )); then
  echo "Nothing to do. Use --force to reload."
  exit 0
fi

# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------
if [[ "$DRY_RUN" == true ]]; then
  names=()
  for i in "${targets[@]}"; do
    printf -v suffix '%03d' "$i"
    names+=("${PDB_PREFIX}_${suffix}")
  done
  echo
  echo "Dry run - nothing will be changed. PDBs that would be loaded:"
  printf '%s ' "${names[@]}" | fold -s -w 100
  echo
  first_pdb="${names[0]}"
  echo "Settings: parallel=${PARALLEL}, threads=${THREADS}, scale=${SCALE}, tablespace=${TABLESPACE}, keep-going=${KEEP_GOING}"
  echo "Command for the first PDB:"
  echo "  $OEWIZARD -cl -create -cs //${SCAN_HOST}:${SCAN_PORT}/${first_pdb,,}${PDB_SERVICE_SUFFIX} -u soe -p '********' \\"
  echo "    -scale ${SCALE} -tc ${THREADS} -dba \"sys as sysdba\" -dbap '********' -ts ${TABLESPACE} -async_off -v"
  exit 0
fi

# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
read_secret() {
  local prompt="$1" var_name="$2" value
  read -r -s -p "$prompt" value
  printf '\n'
  printf -v "$var_name" '%s' "$value"
}

[[ -n "${SOE_PASSWORD:-}" ]] || read_secret "SOE password: " SOE_PASSWORD
[[ -n "${DBA_PASSWORD:-}" ]] || read_secret "SYS password: " DBA_PASSWORD
[[ -n "$SOE_PASSWORD" && -n "$DBA_PASSWORD" ]] || die "Passwords must not be empty."
[[ "$SOE_PASSWORD" != *\"* ]] || die "SOE password must not contain a double quote."

mkdir -p "$LOG_DIR" "$STATUS_DIR"
rm -f -- "$STATUS_DIR"/*.ok "$STATUS_DIR"/*.failed

# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
fmt_time() {
  local s="$1"
  printf '%dm%02ds' $((s / 60)) $((s % 60))
}

report_failure() {
  local pdb="$1" step="$2" log="$3" msg
  : > "${STATUS_DIR}/${pdb}.failed"
  msg="$(printf '%s failed for %s; see %s\n' "$step" "$pdb" "$log"; tail -n 80 "$log" 2>/dev/null || true)"
  printf '%s\n' "$msg" >&2
}

load_pdb() {
  local i="$1" suffix pdb service sql_log wizard_log tag start=$SECONDS
  printf -v suffix '%03d' "$i"
  pdb="${PDB_PREFIX}_${suffix}"
  service="//${SCAN_HOST}:${SCAN_PORT}/${pdb,,}${PDB_SERVICE_SUFFIX}"
  sql_log="${LOG_DIR}/${pdb}-soe-setup.log"
  wizard_log="${LOG_DIR}/${pdb}-oewizard.log"
  tag="[PDB $i; range $FIRST-$LAST]"

  echo "$tag Preparing SOE in ${pdb}"
  if ! sqlplus -L -s / as sysdba >"$sql_log" 2>&1 <<SQL
WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET DEFINE OFF
SET ECHO OFF
ALTER SESSION SET CONTAINER = ${pdb};
GRANT DBA TO soe IDENTIFIED BY "${SOE_PASSWORD}";
EXIT SUCCESS
SQL
  then
    report_failure "$pdb" "SOE setup" "$sql_log"
    exit 1
  fi

  echo "$tag Loading SOE schema into ${service}"
  if ! "$OEWIZARD" \
      -cl -create \
      -cs "$service" \
      -u soe -p "$SOE_PASSWORD" \
      -scale "$SCALE" -tc "$THREADS" \
      -dba "sys as sysdba" -dbap "$DBA_PASSWORD" \
      -ts "$TABLESPACE" \
      -async_off -v \
      >"$wizard_log" 2>&1
  then
    report_failure "$pdb" "Swingbench" "$wizard_log"
    exit 1
  fi

  printf '%s\n' "${pdb_uid[$pdb]}" > "${LOG_DIR}/${pdb}.done"
  : > "${STATUS_DIR}/${pdb}.ok"
  echo "$tag Finished ${pdb} in $(fmt_time $((SECONDS - start)))"
}

# ---------------------------------------------------------------------------
# Ctrl-C / SIGTERM: stop the workers and everything they started (sqlplus, oewizard).
# ---------------------------------------------------------------------------
kill_tree() {
  local pid="$1" child kids
  kids="$(pgrep -P "$pid" 2>/dev/null || true)"
  kill "$pid" 2>/dev/null || true
  for child in $kids; do
    kill_tree "$child"
  done
}

cleanup() {
  local pid
  trap - INT TERM
  printf '\nInterrupted: stopping running loads...\n' >&2
  for pid in $(jobs -rp); do
    kill_tree "$pid"
  done
  wait 2>/dev/null || true
  exit 130
}
trap cleanup INT TERM

count_failed() {
  local f
  shopt -s nullglob
  f=("$STATUS_DIR"/*.failed)
  echo "${#f[@]}"
}

# ---------------------------------------------------------------------------
# Run: sliding window of up to $PARALLEL concurrent loads.
# ---------------------------------------------------------------------------
echo "Loading ${#targets[@]} PDB(s): up to ${PARALLEL} at a time, ${THREADS} threads each, scale ${SCALE}."
run_start=$SECONDS

for i in "${targets[@]}"; do
  while (( $(jobs -rp | wc -l) >= PARALLEL )); do
    sleep 1
  done
  if [[ "$KEEP_GOING" != true ]] && (( $(count_failed) > 0 )); then
    echo "A load failed; not starting any more PDBs (use --keep-going to continue)." >&2
    break
  fi
  load_pdb "$i" &
done
wait

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
shopt -s nullglob
ok_files=("$STATUS_DIR"/*.ok)
failed_files=("$STATUS_DIR"/*.failed)
n_ok=${#ok_files[@]}
n_failed=${#failed_files[@]}
n_not_started=$(( ${#targets[@]} - n_ok - n_failed ))

echo
echo "Finished in $(fmt_time $((SECONDS - run_start))): ${n_ok} loaded, ${#already_done[@]} already loaded (skipped), ${n_failed} failed, ${n_not_started} not started."

if (( n_failed > 0 )); then
  echo "Failed PDBs:" >&2
  for f in "${failed_files[@]}"; do
    b="${f##*/}"
    echo "  ${b%.failed}  (logs: ${LOG_DIR}/${b%.failed}-*.log)" >&2
  done
  echo "Run the same command again to retry them; loaded PDBs are skipped." >&2
  exit 1
fi
if (( n_not_started > 0 )); then
  echo "Some PDBs were not started. Run the same command again to continue." >&2
  exit 1
fi

echo "Completed Swingbench loading."
