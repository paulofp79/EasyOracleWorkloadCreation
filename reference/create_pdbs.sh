#!/usr/bin/env bash
set -Eeuo pipefail

# Override at launch: FIRST=220 LAST=250 bash create_pdbs.sh
FIRST="${FIRST:-1}"
LAST="${LAST:-250}"
PDB_PREFIX="PDBPP"

# TDE wallet / master key per PDB.
#   USE_WALLET=true   -> create a TDE master key in each new PDB (needs a configured wallet)
#   USE_WALLET=false  -> skip all TDE steps (for CDBs without a wallet)
#   unset             -> the script asks interactively
# Example: USE_WALLET=false FIRST=1 LAST=50 bash create_pdbs.sh
USE_WALLET="${USE_WALLET:-}"

# The SCAN hostname suggests RAC. Set to false for a single-instance CDB.
RAC_CDB=true

LOG_DIR="${LOG_DIR:-./pdb-create-logs}"

read_secret() {
  local prompt="$1"
  local var_name="$2"
  local value
  read -r -s -p "$prompt" value
  printf '\n'
  printf -v "$var_name" '%s' "$value"
}

USE_WALLET="${USE_WALLET,,}"
case "$USE_WALLET" in
  yes|y) USE_WALLET=true ;;
  no|n)  USE_WALLET=false ;;
esac

if [[ -z "$USE_WALLET" ]]; then
  if [[ -t 0 ]]; then
    while true; do
      read -r -p "Is a TDE wallet configured? Create a master key in each PDB? [y/n]: " wallet_answer
      case "${wallet_answer,,}" in
        y|yes) USE_WALLET=true;  break ;;
        n|no)  USE_WALLET=false; break ;;
        *)     echo "Please answer y or n." ;;
      esac
    done
  else
    echo "USE_WALLET is not set and there is no terminal to ask. Run with USE_WALLET=true or USE_WALLET=false." >&2
    exit 1
  fi
fi

if [[ "$USE_WALLET" != "true" && "$USE_WALLET" != "false" ]]; then
  echo "USE_WALLET must be true or false." >&2
  exit 1
fi

[[ -n "${PDB_ADMIN_PASSWORD:-}" ]] || read_secret "PDB admin password: " PDB_ADMIN_PASSWORD
export PDB_ADMIN_PASSWORD

if [[ "$USE_WALLET" == "true" ]]; then
  [[ -n "${TDE_WALLET_PASSWORD:-}" ]] || read_secret "TDE wallet password: " TDE_WALLET_PASSWORD
  export TDE_WALLET_PASSWORD
fi

command -v sqlplus >/dev/null || {
  echo "sqlplus not found in PATH" >&2
  exit 1
}

if [[ ! "$PDB_PREFIX" =~ ^[A-Za-z][A-Za-z0-9_]*$ ]] || (( ${#PDB_PREFIX} > 26 )); then
  echo "PDB_PREFIX must start with a letter, contain only letters/numbers/underscores, and be at most 26 characters." >&2
  exit 1
fi
if [[ ! "$FIRST" =~ ^[1-9][0-9]*$ || ! "$LAST" =~ ^[1-9][0-9]*$ ]]; then
  echo "FIRST and LAST must be positive integers without leading zeros." >&2
  exit 1
fi
if (( FIRST > LAST || LAST > 999 )); then
  echo "Require 1 <= FIRST <= LAST <= 999." >&2
  exit 1
fi
if [[ "$RAC_CDB" != "true" && "$RAC_CDB" != "false" ]]; then
  echo "RAC_CDB must be true or false." >&2
  exit 1
fi

mkdir -p "$LOG_DIR"

echo "TDE wallet / master key per PDB: ${USE_WALLET}"

# Build the exact PDB name range and SQL IN-list.
declare -a pdbs=()
declare -A existing_set=()
pdb_in_list=""

for ((i = FIRST; i <= LAST; i++)); do
  printf -v suffix '%03d' "$i"
  pdb="${PDB_PREFIX}_${suffix}"
  pdb="${pdb^^}"
  pdbs+=("$pdb")

  if [[ -n "$pdb_in_list" ]]; then
    pdb_in_list+=","
  fi
  pdb_in_list+="'${pdb}'"
done

echo "Checking existing PDBs from ${PDB_PREFIX}_${FIRST} through ${PDB_PREFIX}_${LAST}..."

if ! existing_pdbs_raw="$(sqlplus -L -s / as sysdba <<SQL
WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET DEFINE OFF
SET PAGESIZE 0 FEEDBACK OFF HEADING OFF VERIFY OFF ECHO OFF
SET LINESIZE 200

SELECT name
FROM   v\$pdbs
WHERE  name IN (${pdb_in_list})
ORDER  BY name;

EXIT
SQL
)"; then
  echo "Could not check existing PDBs. SQL*Plus output:" >&2
  printf '%s\n' "$existing_pdbs_raw" >&2
  exit 1
fi

existing_pdbs="$(printf '%s\n' "$existing_pdbs_raw" | awk 'NF { print toupper($1) }')"

if [[ -n "$existing_pdbs" ]]; then
  echo
  echo "Existing PDBs found:"
  printf '%s\n' "$existing_pdbs"
  echo
  echo "D will close and drop these PDBs, including their datafiles."
  echo "S will leave them in place and skip them."
  echo "A will abort."
  read -r -p "Choose [D/S/A]: " action

  case "${action^^}" in
    D)
      echo
      echo "This permanently removes the listed PDBs and their datafiles."
      read -r -p "Type DELETE to confirm: " confirmation
      if [[ "$confirmation" != "DELETE" ]]; then
        echo "Confirmation did not match. Aborting without changes."
        exit 1
      fi

      while IFS= read -r existing_pdb; do
        [[ -n "$existing_pdb" ]] && existing_set["$existing_pdb"]=1
      done <<< "$existing_pdbs"

      drop_sql_file="${LOG_DIR}/drop-existing-pdbs.sql"
      drop_log="${LOG_DIR}/drop-existing-pdbs.log"

      {
        printf '%s\n' \
          "WHENEVER OSERROR EXIT FAILURE" \
          "WHENEVER SQLERROR EXIT SQL.SQLCODE"

        for pdb in "${pdbs[@]}"; do
          if [[ -n "${existing_set[$pdb]:-}" ]]; then
            if [[ "$RAC_CDB" == "true" ]]; then
              cat <<SQL
DECLARE
  l_open_instances PLS_INTEGER;
BEGIN
  SELECT COUNT(*)
  INTO   l_open_instances
  FROM   gv\$pdbs
  WHERE  name = '${pdb}'
  AND    open_mode <> 'MOUNTED';

  IF l_open_instances > 0 THEN
    EXECUTE IMMEDIATE
      'ALTER PLUGGABLE DATABASE ${pdb} CLOSE IMMEDIATE INSTANCES=ALL';
  END IF;
END;
/

SQL
            else
              cat <<SQL
DECLARE
  l_open_instances PLS_INTEGER;
BEGIN
  SELECT COUNT(*)
  INTO   l_open_instances
  FROM   gv\$pdbs
  WHERE  name = '${pdb}'
  AND    open_mode <> 'MOUNTED';

  IF l_open_instances > 0 THEN
    EXECUTE IMMEDIATE
      'ALTER PLUGGABLE DATABASE ${pdb} CLOSE IMMEDIATE';
  END IF;
END;
/

SQL
            fi

            printf 'DROP PLUGGABLE DATABASE %s INCLUDING DATAFILES;\n\n' "$pdb"
          fi
        done

        printf '%s\n' "EXIT"
      } > "$drop_sql_file"

      echo "Closing and dropping existing PDBs..."
      if ! sqlplus -L -s / as sysdba @"$drop_sql_file" >"$drop_log" 2>&1; then
        echo "Drop failed; see ${drop_log}" >&2
        tail -n 80 "$drop_log" >&2 || true
        exit 1
      fi

      existing_set=()
      echo "Existing PDBs dropped."
      ;;

    S)
      while IFS= read -r existing_pdb; do
        [[ -n "$existing_pdb" ]] && existing_set["$existing_pdb"]=1
      done <<< "$existing_pdbs"
      echo "Existing PDBs will be skipped."
      ;;

    A)
      echo "Aborted without changes."
      exit 1
      ;;

    *)
      echo "Invalid choice. Aborting without changes." >&2
      exit 1
      ;;
  esac
fi

for ((i = FIRST; i <= LAST; i++)); do
  printf -v suffix '%03d' "$i"
  pdb="${PDB_PREFIX}_${suffix}"
  pdb="${pdb^^}"

  if [[ -n "${existing_set[$pdb]:-}" ]]; then
    echo "[PDB $i; range $FIRST-$LAST] Skipping existing ${pdb}"
    continue
  fi

  sql_log="${LOG_DIR}/${pdb}-create.log"

  echo "[PDB $i; range $FIRST-$LAST] Creating and opening ${pdb}"

  if [[ "$RAC_CDB" == "true" ]]; then
    open_sql="ALTER PLUGGABLE DATABASE ${pdb} OPEN INSTANCES=ALL;"
    save_sql=""
  else
    open_sql="ALTER PLUGGABLE DATABASE ${pdb} OPEN;"
    save_sql="ALTER PLUGGABLE DATABASE ${pdb} SAVE STATE;"
  fi

  # TDE master key is only created when USE_WALLET=true.
  if [[ "$USE_WALLET" == "true" ]]; then
    tde_sql="ALTER SESSION SET CONTAINER = ${pdb};

ADMINISTER KEY MANAGEMENT SET KEY
  FORCE KEYSTORE
  IDENTIFIED BY \"${TDE_WALLET_PASSWORD}\"
  WITH BACKUP
  CONTAINER = CURRENT;"
  else
    tde_sql=""
  fi

  if ! sqlplus -L -s / as sysdba >"$sql_log" 2>&1 <<SQL
WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET DEFINE OFF

CREATE PLUGGABLE DATABASE ${pdb}
  ADMIN USER pdbadmin IDENTIFIED BY "${PDB_ADMIN_PASSWORD}";

${open_sql}
${save_sql}

ALTER SYSTEM REGISTER;

${tde_sql}

EXIT
SQL
  then
    echo "PDB setup failed for ${pdb}; see ${sql_log}" >&2
    tail -n 80 "$sql_log" >&2 || true
    exit 1
  fi

  echo "[PDB $i; range $FIRST-$LAST] Finished ${pdb}"
done

echo "Completed PDB processing."
