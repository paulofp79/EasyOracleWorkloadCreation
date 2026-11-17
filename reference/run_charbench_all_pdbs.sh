#!/usr/bin/env bash
#
# run_charbench_all_pdbs.sh - check the connection to every PDB, then run
# charbench on all of them at the same time.
# Run with --help for all options.
set -Eeuo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
  echo "bash 4 or newer is required." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Defaults. Environment variables override these; command-line flags override both.
# Keep FIRST/LAST aligned with the PDBs you created and loaded.
# ---------------------------------------------------------------------------
FIRST="${FIRST:-1}"
LAST="${LAST:-250}"
PDB_PREFIX="${PDB_PREFIX:-PDBPP}"
# Default to the SCAN and service names used for your PDBPP loads.
SCAN_HOST="${SCAN_HOST:-scaqaq01-scan1.us.oracle.com}"
SCAN_PORT="${SCAN_PORT:-1521}"
PDB_SERVICE_SUFFIX="${PDB_SERVICE_SUFFIX:-}"
CHARBENCH="${CHARBENCH:-/home/oracle/swingbench/bin/charbench}"
CONFIG="${CONFIG:-/home/oracle/swingbench/configs/SOE_Server_Side_V2.xml}"
LOG_DIR="${LOG_DIR:-/home/oracle/charbench-logs}"
USERS="${USERS:-8}"
RUNTIME="${RUNTIME:-00:30}"
DISABLE_TXNS="${DISABLE_TXNS-SQ,WQ,WA}"
CONNECT_TIMEOUT="${CONNECT_TIMEOUT:-30}"
CONNECT_PARALLEL="${CONNECT_PARALLEL:-16}"
START_DELAY="${START_DELAY:-0}"
SKIP_UNREACHABLE="${SKIP_UNREACHABLE:-false}"
CHECK_ONLY="${CHECK_ONLY:-false}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: ${0##*/} [options]

Step 1: logs in as SOE to every PDB <PREFIX>_<FIRST> .. <PREFIX>_<LAST>
        (e.g. ${PDB_PREFIX}_$(printf '%03d' "$FIRST") .. ${PDB_PREFIX}_$(printf '%03d' "$LAST")) and checks that the SOE schema is there.
Step 2: if every PDB passed, starts one charbench per PDB, all at once, and waits.

PDB range
  -f, --first N            first PDB number                      [FIRST=${FIRST}]
  -l, --last N             last PDB number (max 999)             [LAST=${LAST}]
  -p, --prefix NAME        PDB name prefix                       [PDB_PREFIX=${PDB_PREFIX}]

Connection
      --scan-host HOST     SCAN (or host) used for the services  [SCAN_HOST=${SCAN_HOST}]
      --scan-port PORT     listener port                         [SCAN_PORT=${SCAN_PORT}]
      --service-suffix S   appended to the lower-case PDB name to
                           build the service, e.g. ".us.oracle.com"
                                                                 [PDB_SERVICE_SUFFIX="${PDB_SERVICE_SUFFIX}"]

Workload
  -u, --users N            charbench users per PDB (-uc)         [USERS=${USERS}]
  -r, --runtime HH:MM      run time per PDB (-rt)                [RUNTIME=${RUNTIME}]
  -c, --config FILE        Swingbench config XML (-c)            [CONFIG=${CONFIG}]
  -b, --charbench PATH     path to the charbench executable      [CHARBENCH=${CHARBENCH}]
  -d, --disable LIST       transactions to disable (-di); use ""
                           to disable none                       [DISABLE_TXNS=${DISABLE_TXNS}]
      --start-delay SEC    pause between starting two workloads, to
                           soften a connection storm (0 = none)  [START_DELAY=${START_DELAY}]

Connection check
      --connect-timeout S  give up on a PDB after S seconds      [CONNECT_TIMEOUT=${CONNECT_TIMEOUT}]
      --connect-parallel N PDBs checked at the same time         [CONNECT_PARALLEL=${CONNECT_PARALLEL}]
      --check-only         only run the connection check; start no workload
      --skip-unreachable   run the workload on the PDBs that passed
                           (default: any failure stops the whole run)

Other
  -L, --log-dir DIR        parent directory for run logs         [LOG_DIR=${LOG_DIR}]
  -h, -help, --help        show this help

The SOE password is never taken from the command line. Set SOE_PASSWORD in the
environment, or you will be prompted. (charbench itself only accepts the password
as an argument, so it is visible in "ps" on this host while the workload runs.)

What the connection check verifies, per PDB
  * SOE can log in through //SCAN:PORT/<pdb service>, within the timeout
  * the session is really in that PDB (CON_NAME)
  * the core SOE tables exist (CUSTOMERS, ORDERS, ORDER_ITEMS, PRODUCT_INFORMATION)

Each run writes to LOG_DIR/run-<timestamp>-XXXXXX (also linked as LOG_DIR/latest):
  connections.tsv   result of the connection check for every PDB
  processes.tsv     PDB, PID and service of each charbench
  status.tsv        exit code and duration of each charbench
  summary.tsv       exit code plus error lines found in each charbench log
  <PDB>-charbench.log, <PDB>-results.xml, connection-check/<PDB>.log

Ctrl-C stops the check or the workloads, including their Java processes.
Exit status is 1 if the connection check fails (without --skip-unreachable)
or any charbench exits with an error.

Examples
  ${0##*/} --check-only
  ${0##*/} --first 3 --last 38 --users 16 --runtime 01:00
  ${0##*/} -f 1 -l 250 --start-delay 0.5 --skip-unreachable
  SOE_PASSWORD=... ${0##*/} -f 3 -l 38 -r 00:10
EOF
}

need_value() {
  # "-d" may legitimately be given an empty value; every other option needs one.
  if [[ "$1" == "-d" || "$1" == "--disable" ]]; then
    [[ $# -ge 2 ]] || die "Option $1 requires a value (try --help)."
  else
    [[ $# -ge 2 && -n "$2" ]] || die "Option $1 requires a value (try --help)."
  fi
}

while (( $# )); do
  case "$1" in
    -h|-help|--help)      usage; exit 0 ;;
    --*=*)                set -- "${1%%=*}" "${1#*=}" "${@:2}"; continue ;;
    -f|--first)           need_value "$@"; FIRST="$2";              shift 2 ;;
    -l|--last)            need_value "$@"; LAST="$2";               shift 2 ;;
    -p|--prefix)          need_value "$@"; PDB_PREFIX="$2";         shift 2 ;;
    --scan-host)          need_value "$@"; SCAN_HOST="$2";          shift 2 ;;
    --scan-port)          need_value "$@"; SCAN_PORT="$2";          shift 2 ;;
    --service-suffix)     need_value "$@"; PDB_SERVICE_SUFFIX="$2"; shift 2 ;;
    -u|--users)           need_value "$@"; USERS="$2";              shift 2 ;;
    -r|--runtime)         need_value "$@"; RUNTIME="$2";            shift 2 ;;
    -c|--config)          need_value "$@"; CONFIG="$2";             shift 2 ;;
    -b|--charbench)       need_value "$@"; CHARBENCH="$2";          shift 2 ;;
    -d|--disable)         need_value "$@"; DISABLE_TXNS="$2";       shift 2 ;;
    --start-delay)        need_value "$@"; START_DELAY="$2";        shift 2 ;;
    --connect-timeout)    need_value "$@"; CONNECT_TIMEOUT="$2";    shift 2 ;;
    --connect-parallel)   need_value "$@"; CONNECT_PARALLEL="$2";   shift 2 ;;
    -L|--log-dir)         need_value "$@"; LOG_DIR="$2";            shift 2 ;;
    --check-only)         CHECK_ONLY=true;                          shift ;;
    --skip-unreachable)   SKIP_UNREACHABLE=true;                    shift ;;
    *)                    die "Unknown option: $1 (try --help)." ;;
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
[[ "$USERS" =~ ^[1-9][0-9]*$ ]] \
  || die "--users must be a positive integer."
[[ "$RUNTIME" =~ ^[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?$ ]] \
  || die "--runtime must look like HH:MM, e.g. 00:30."
[[ "$SCAN_PORT" =~ ^[0-9]+$ ]] && (( SCAN_PORT >= 1 && SCAN_PORT <= 65535 )) \
  || die "--scan-port must be a number between 1 and 65535."
[[ "$SCAN_HOST" =~ ^[A-Za-z0-9._-]+$ ]] \
  || die "--scan-host contains invalid characters."
[[ "$PDB_SERVICE_SUFFIX" =~ ^[A-Za-z0-9._-]*$ ]] \
  || die "--service-suffix contains invalid characters."
[[ "$DISABLE_TXNS" =~ ^[A-Za-z0-9,]*$ ]] \
  || die "--disable must be a comma-separated list of transaction short names, e.g. SQ,WQ,WA."
[[ "$CONNECT_TIMEOUT" =~ ^[1-9][0-9]*$ ]] \
  || die "--connect-timeout must be a positive integer (seconds)."
[[ "$CONNECT_PARALLEL" =~ ^[1-9][0-9]*$ ]] \
  || die "--connect-parallel must be a positive integer."
[[ "$START_DELAY" =~ ^[0-9]+(\.[0-9]+)?$ ]] \
  || die "--start-delay must be a number of seconds, e.g. 0 or 0.5."
[[ "$SKIP_UNREACHABLE" == true || "$SKIP_UNREACHABLE" == false ]] \
  || die "SKIP_UNREACHABLE must be true or false."
[[ "$CHECK_ONLY" == true || "$CHECK_ONLY" == false ]] \
  || die "CHECK_ONLY must be true or false."

[[ -x "$CHARBENCH" ]] || die "Not executable: $CHARBENCH"
[[ -r "$CONFIG" ]] || die "Cannot read config: $CONFIG"
command -v sqlplus >/dev/null || die "sqlplus not found in PATH (needed for the connection check)."
command -v timeout >/dev/null || die "timeout is required (GNU coreutils)."
# A separate process group per workload allows Ctrl+C to stop Java children too.
command -v setsid >/dev/null || die "setsid is required (Linux util-linux)."

# Resolve paths before changing to Swingbench's bin directory.
charbench_dir="$(cd -- "$(dirname -- "$CHARBENCH")" && pwd)"
CHARBENCH="${charbench_dir}/$(basename -- "$CHARBENCH")"
config_dir="$(cd -- "$(dirname -- "$CONFIG")" && pwd)"
CONFIG="${config_dir}/$(basename -- "$CONFIG")"
mkdir -p "$LOG_DIR"
LOG_DIR="$(cd -- "$LOG_DIR" && pwd)"

# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------
if [[ -z "${SOE_PASSWORD:-}" ]]; then
  read -r -s -p "SOE password: " SOE_PASSWORD
  printf '\n'
fi
[[ -n "$SOE_PASSWORD" ]] || die "The SOE password must not be empty."
[[ "$SOE_PASSWORD" != *\"* ]] || die "The SOE password must not contain a double quote."

RUN_DIR="$(mktemp -d "${LOG_DIR}/run-$(date +%Y%m%d-%H%M%S)-XXXXXX")"
CONN_DIR="${RUN_DIR}/connection-check"
mkdir -p "$CONN_DIR"
ln -sfn "$RUN_DIR" "${LOG_DIR}/latest"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
fmt_time() {
  local s="$1"
  printf '%dm%02ds' $((s / 60)) $((s % 60))
}

service_for() {
  local pdb="$1"
  printf '//%s:%s/%s%s' "$SCAN_HOST" "$SCAN_PORT" "${pdb,,}" "$PDB_SERVICE_SUFFIX"
}

# Signal a background job: its own process group if it has one (the charbench
# workloads), otherwise the process and its children (the connection checks).
kill_job() {
  local pid="$1" sig="$2" kids child
  if kill -"$sig" -- "-$pid" 2>/dev/null; then
    return 0
  fi
  kids="$(pgrep -P "$pid" 2>/dev/null || true)"
  kill -"$sig" "$pid" 2>/dev/null || true
  for child in $kids; do
    kill_job "$child" "$sig"
  done
}

stop_everything() {
  local code="$1" pid running
  trap '' INT TERM
  printf '\nInterrupted: stopping running processes...\n' >&2
  running="$(jobs -rp)"
  for pid in $running; do kill_job "$pid" TERM; done
  sleep 2
  for pid in $running; do kill_job "$pid" KILL; done
  wait 2>/dev/null || true
  echo "Logs: $RUN_DIR" >&2
  exit "$code"
}
trap 'stop_everything 130' INT
trap 'stop_everything 143' TERM

# ---------------------------------------------------------------------------
# Step 1: connection check
# ---------------------------------------------------------------------------
declare -a all_pdbs=()
for ((i = FIRST; i <= LAST; i++)); do
  printf -v suffix '%03d' "$i"
  all_pdbs+=("${PDB_PREFIX}_${suffix}")
done

# Logs in as SOE, confirms the container and the SOE tables, and writes
# "OK<TAB>instance" or "FAIL<TAB>reason" to CONN_DIR/<pdb>.result.
check_pdb() {
  local pdb="$1" service out rc=0 line con inst ntab reason status detail
  service="$(service_for "$pdb")"

  out="$(timeout -k 5 "$CONNECT_TIMEOUT" sqlplus -L -s /nolog 2>&1 <<SQL
WHENEVER SQLERROR EXIT FAILURE
SET DEFINE OFF
SET PAGESIZE 0 FEEDBACK OFF HEADING OFF VERIFY OFF ECHO OFF
SET LINESIZE 300 TRIMSPOOL ON
CONNECT soe/"${SOE_PASSWORD}"@${service}
SELECT 'SOE_CHECK_OK|' || SYS_CONTEXT('USERENV','CON_NAME') || '|' || SYS_CONTEXT('USERENV','INSTANCE_NAME') || '|' || (SELECT COUNT(*) FROM user_tables WHERE table_name IN ('CUSTOMERS','ORDERS','ORDER_ITEMS','PRODUCT_INFORMATION')) FROM dual;
EXIT
SQL
)" || rc=$?
  printf '%s\n' "$out" > "${CONN_DIR}/${pdb}.log"

  line="$(grep -m1 '^SOE_CHECK_OK|' <<<"$out" || true)"
  if [[ -n "$line" ]]; then
    IFS='|' read -r _ con inst ntab <<<"$line"
    con="${con//[[:space:]]/}"; inst="${inst//[[:space:]]/}"; ntab="${ntab//[[:space:]]/}"
    if [[ "${con^^}" != "$pdb" ]]; then
      status=FAIL; detail="connected to container ${con}, expected ${pdb}"
    elif [[ "$ntab" != 4 ]]; then
      status=FAIL; detail="SOE schema missing or incomplete (${ntab}/4 core tables)"
    else
      status=OK; detail="$inst"
    fi
  else
    status=FAIL
    if (( rc == 124 || rc == 137 )); then
      detail="timed out after ${CONNECT_TIMEOUT}s"
    else
      reason="$(grep -m1 -E '(ORA|TNS|SP2)-[0-9]+' <<<"$out" || true)"
      [[ -n "$reason" ]] || reason="$(grep -m1 -v '^[[:space:]]*$' <<<"$out" || true)"
      [[ -n "$reason" ]] || reason="no output from sqlplus (exit code ${rc})"
      detail="$reason"
    fi
  fi
  detail="${detail//$'\t'/ }"
  printf '%s\t%s\n' "$status" "${detail:0:200}" > "${CONN_DIR}/${pdb}.result"
}

echo "Checking SOE connections to ${#all_pdbs[@]} PDBs (${all_pdbs[0]} .. ${all_pdbs[${#all_pdbs[@]}-1]}), ${CONNECT_PARALLEL} at a time, timeout ${CONNECT_TIMEOUT}s..."
check_start=$SECONDS
for pdb in "${all_pdbs[@]}"; do
  while (( $(jobs -rp | wc -l) >= CONNECT_PARALLEL )); do
    sleep 0.2
  done
  check_pdb "$pdb" &
done
wait

declare -a good_pdbs=()
declare -a bad_pdbs=()
declare -a bad_detail=()
printf 'pdb\tstatus\tdetail\n' > "${RUN_DIR}/connections.tsv"
for pdb in "${all_pdbs[@]}"; do
  if [[ -f "${CONN_DIR}/${pdb}.result" ]]; then
    IFS=$'\t' read -r status detail < "${CONN_DIR}/${pdb}.result" || true
  else
    status=FAIL; detail="no result recorded"
  fi
  printf '%s\t%s\t%s\n' "$pdb" "$status" "${detail:-}" >> "${RUN_DIR}/connections.tsv"
  if [[ "$status" == OK ]]; then
    good_pdbs+=("$pdb")
  else
    bad_pdbs+=("$pdb")
    bad_detail+=("${detail:-}")
  fi
done

echo "Connection check finished in $(fmt_time $((SECONDS - check_start))): ${#good_pdbs[@]} OK, ${#bad_pdbs[@]} failed."

if (( ${#bad_pdbs[@]} > 0 )); then
  echo "Failed PDBs:" >&2
  for ((k = 0; k < ${#bad_pdbs[@]} && k < 20; k++)); do
    printf '  %-14s %s\n' "${bad_pdbs[$k]}" "${bad_detail[$k]}" >&2
  done
  if (( ${#bad_pdbs[@]} > 20 )); then
    echo "  ... and $(( ${#bad_pdbs[@]} - 20 )) more; see ${RUN_DIR}/connections.tsv" >&2
  fi

  if [[ "$CHECK_ONLY" == true ]]; then
    echo "Check only: no workload started. Details: ${RUN_DIR}/connections.tsv" >&2
    exit 1
  fi
  if [[ "$SKIP_UNREACHABLE" != true ]]; then
    die "Not starting any workload because ${#bad_pdbs[@]} PDB(s) failed the check. Fix them, or use --skip-unreachable to run on the ${#good_pdbs[@]} that passed."
  fi
  (( ${#good_pdbs[@]} > 0 )) || die "No PDB passed the connection check; nothing to run."
  echo "Continuing with the ${#good_pdbs[@]} PDB(s) that passed (--skip-unreachable)."
fi

if [[ "$CHECK_ONLY" == true ]]; then
  echo "Check only: all ${#good_pdbs[@]} PDBs are reachable. No workload started."
  exit 0
fi

# ---------------------------------------------------------------------------
# Step 2: workload
# ---------------------------------------------------------------------------
cd "$charbench_dir"
declare -a pids=() names=() started=()
printf 'pdb\tpid\tservice\n' > "$RUN_DIR/processes.tsv"
printf 'pdb\texit_code\tseconds\n' > "$RUN_DIR/status.tsv"

declare -a di_args=()
if [[ -n "$DISABLE_TXNS" ]]; then
  di_args=(-di "$DISABLE_TXNS")
fi

echo "Starting ${#good_pdbs[@]} workloads: ${USERS} users per PDB, runtime ${RUNTIME}."
echo "Logs and results: $RUN_DIR"

# Launch every workload before waiting for any of them.
last_index=$(( ${#good_pdbs[@]} - 1 ))
for idx in "${!good_pdbs[@]}"; do
  pdb="${good_pdbs[$idx]}"
  service="$(service_for "$pdb")"
  setsid "$CHARBENCH" \
    -cs "$service" \
    -u soe -p "$SOE_PASSWORD" \
    -c "$CONFIG" \
    -v users,tpm,tps,errs,vresp \
    -intermin 0 -intermax 0 -min 0 -max 0 \
    -uc "$USERS" ${di_args[@]+"${di_args[@]}"} -rt "$RUNTIME" \
    -r "$RUN_DIR/${pdb}-results.xml" \
    > "$RUN_DIR/${pdb}-charbench.log" 2>&1 &
  pid=$!
  pids+=("$pid")
  names+=("$pdb")
  started+=("$SECONDS")
  printf '%s\t%s\t%s\n' "$pdb" "$pid" "$service" >> "$RUN_DIR/processes.tsv"
  echo "Started ${pdb}, PID ${pid}"
  if (( idx < last_index )) && [[ "$START_DELAY" =~ [1-9] ]]; then
    sleep "$START_DELAY"
  fi
done

echo "All workloads launched. Waiting for completion; Ctrl+C stops this run."

# Report each workload as soon as it ends, not in launch order.
declare -A pending=()
for j in "${!pids[@]}"; do
  pending["$j"]=1
done
failed=0
last_beat=$SECONDS
while (( ${#pending[@]} > 0 )); do
  running=" $(jobs -rp | tr '\n' ' ') "
  for j in "${!pending[@]}"; do
    pid="${pids[$j]}"
    if [[ "$running" != *" ${pid} "* ]]; then
      if wait "$pid"; then rc=0; else rc=$?; fi
      dur=$(( SECONDS - started[j] ))
      unset 'pending[$j]'
      if (( rc == 0 )); then
        echo "Finished ${names[$j]} in $(fmt_time "$dur")"
      else
        failed=$((failed + 1))
        echo "FAILED: ${names[$j]}, exit ${rc}; see ${RUN_DIR}/${names[$j]}-charbench.log" >&2
      fi
      printf '%s\t%s\t%s\n' "${names[$j]}" "$rc" "$dur" >> "$RUN_DIR/status.tsv"
    fi
  done
  if (( ${#pending[@]} > 0 )); then
    if (( SECONDS - last_beat >= 60 )); then
      echo "... ${#pending[@]} of ${#pids[@]} workloads still running ($(fmt_time $((SECONDS - started[0]))) elapsed)"
      last_beat=$SECONDS
    fi
    sleep 1
  fi
done
trap - INT TERM

# ---------------------------------------------------------------------------
# Summary: exit codes alone do not show database errors, so scan the logs too.
# ---------------------------------------------------------------------------
ERR_REGEX='ORA-[0-9]{5}|TNS-[0-9]{5}|SQLException|Exception|Connection refused|Connection reset'
printf 'pdb\texit_code\terror_lines\tfirst_error\n' > "$RUN_DIR/summary.tsv"
declare -a with_errors=()
declare -a with_errors_first=()
while IFS=$'\t' read -r pdb rc dur; do
  [[ "$pdb" == pdb ]] && continue
  log="$RUN_DIR/${pdb}-charbench.log"
  nerr="$(grep -c -E "$ERR_REGEX" "$log" 2>/dev/null || true)"
  first="$(grep -m1 -E "$ERR_REGEX" "$log" 2>/dev/null || true)"
  first="${first//$'\t'/ }"
  printf '%s\t%s\t%s\t%s\n' "$pdb" "$rc" "${nerr:-0}" "${first:0:150}" >> "$RUN_DIR/summary.tsv"
  if (( ${nerr:-0} > 0 )); then
    with_errors+=("$pdb")
    with_errors_first+=("${first:0:150}")
  fi
done < "$RUN_DIR/status.tsv"

echo
echo "Finished: $(( ${#pids[@]} - failed )) workloads exited normally, ${failed} failed."
if (( ${#with_errors[@]} > 0 )); then
  echo "Error text found in the charbench logs of ${#with_errors[@]} PDB(s):"
  for ((k = 0; k < ${#with_errors[@]} && k < 20; k++)); do
    printf '  %-14s %s\n' "${with_errors[$k]}" "${with_errors_first[$k]}"
  done
  if (( ${#with_errors[@]} > 20 )); then
    echo "  ... and $(( ${#with_errors[@]} - 20 )) more; see ${RUN_DIR}/summary.tsv"
  fi
else
  echo "No error text found in the charbench logs."
fi
echo "Logs and results: $RUN_DIR"
if (( failed > 0 )); then
  exit 1
fi
