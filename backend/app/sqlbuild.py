"""SQL text sent to sqlplus on stdin. Only whitelisted operations; inputs must be validated first.

Texts that exist in reference/*.sh are reproduced exactly and pinned by golden tests
(tests/test_sqlbuild.py extracts the heredocs from the scripts and compares).
"""
from __future__ import annotations

ENV_CHECK_TOKEN = "ENV_CHECK|"
PDB_TOKEN = "PDB|"
SOE_CHECK_TOKEN = "SOE_CHECK_OK|"
SOE_CORE_TABLES = ("CUSTOMERS", "ORDERS", "ORDER_ITEMS", "PRODUCT_INFORMATION")

_HEADER = """WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET DEFINE OFF
SET PAGESIZE 0 FEEDBACK OFF HEADING OFF VERIFY OFF ECHO OFF
SET LINESIZE 400 TRIMSPOOL ON
"""


def env_check_sql() -> str:
    """CDB name, version, instance count, CDB flag, current container, cluster_database."""
    return _HEADER + """
SELECT 'ENV_CHECK|' || d.name || '|' || i.version || '|' ||
       (SELECT COUNT(*) FROM gv$instance) || '|' || d.cdb || '|' ||
       SYS_CONTEXT('USERENV','CON_NAME') || '|' ||
       (SELECT UPPER(value) FROM v$parameter WHERE name = 'cluster_database')
FROM   v$database d, v$instance i;

EXIT
"""


def preflight_sql(prefix: str) -> str:
    """create_swingbench.sh pre-flight: name|con_uid|instances open READ WRITE, by prefix (no IN list)."""
    return f"""WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET DEFINE OFF
SET PAGESIZE 0 FEEDBACK OFF HEADING OFF VERIFY OFF ECHO OFF
SET LINESIZE 200 TRIMSPOOL ON

SELECT name || '|' || con_uid || '|' || SUM(CASE WHEN open_mode = 'READ WRITE' THEN 1 ELSE 0 END)
FROM   gv$pdbs
WHERE  name LIKE '{prefix}%'
GROUP  BY name, con_uid
ORDER  BY name;

EXIT
"""


def inventory_sql(prefix: str) -> str:
    """Inventory page: one row per PDB and instance, with the instance name and restricted flag."""
    return _HEADER + f"""
SELECT 'PDB|' || p.name || '|' || p.con_uid || '|' || p.inst_id || '|' || p.open_mode || '|' ||
       i.instance_name || '|' || NVL(p.restricted, 'NO')
FROM   gv$pdbs p
JOIN   gv$instance i ON i.inst_id = p.inst_id
WHERE  p.name LIKE '{prefix}%'
ORDER  BY p.name, p.inst_id;

EXIT
"""


def check_sql(soe_password: str, connect: str) -> str:
    """run_charbench_all_pdbs.sh check_pdb(): SOE login, container and core-table check."""
    tables = ",".join(f"'{t}'" for t in SOE_CORE_TABLES)
    return f"""WHENEVER SQLERROR EXIT FAILURE
SET DEFINE OFF
SET PAGESIZE 0 FEEDBACK OFF HEADING OFF VERIFY OFF ECHO OFF
SET LINESIZE 300 TRIMSPOOL ON
CONNECT soe/"{soe_password}"@{connect}
SELECT 'SOE_CHECK_OK|' || SYS_CONTEXT('USERENV','CON_NAME') || '|' || SYS_CONTEXT('USERENV','INSTANCE_NAME') || '|' || (SELECT COUNT(*) FROM user_tables WHERE table_name IN ({tables})) FROM dual;
EXIT
"""


def create_pdb_sql(pdb: str, admin_password: str, rac: bool, wallet_password: str | None) -> str:
    """create_pdbs.sh per-PDB block. wallet_password=None means 'Use TDE wallet' is off: no TDE statements."""
    if rac:
        open_sql = f"ALTER PLUGGABLE DATABASE {pdb} OPEN INSTANCES=ALL;"
        save_sql = ""
    else:
        open_sql = f"ALTER PLUGGABLE DATABASE {pdb} OPEN;"
        save_sql = f"ALTER PLUGGABLE DATABASE {pdb} SAVE STATE;"
    if wallet_password is not None:
        tde_sql = (f"ALTER SESSION SET CONTAINER = {pdb};\n\n"
                   "ADMINISTER KEY MANAGEMENT SET KEY\n"
                   "  FORCE KEYSTORE\n"
                   f'  IDENTIFIED BY "{wallet_password}"\n'
                   "  WITH BACKUP\n"
                   "  CONTAINER = CURRENT;")
    else:
        tde_sql = ""
    return f"""WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET DEFINE OFF

CREATE PLUGGABLE DATABASE {pdb}
  ADMIN USER pdbadmin IDENTIFIED BY "{admin_password}";

{open_sql}
{save_sql}

ALTER SYSTEM REGISTER;

{tde_sql}

EXIT
"""


def drop_pdb_block(pdb: str, rac: bool) -> str:
    """create_pdbs.sh drop block: close the PDB where it is open, then drop it including datafiles."""
    close = f"ALTER PLUGGABLE DATABASE {pdb} CLOSE IMMEDIATE INSTANCES=ALL" if rac else \
        f"ALTER PLUGGABLE DATABASE {pdb} CLOSE IMMEDIATE"
    return f"""DECLARE
  l_open_instances PLS_INTEGER;
BEGIN
  SELECT COUNT(*)
  INTO   l_open_instances
  FROM   gv$pdbs
  WHERE  name = '{pdb}'
  AND    open_mode <> 'MOUNTED';

  IF l_open_instances > 0 THEN
    EXECUTE IMMEDIATE
      '{close}';
  END IF;
END;
/

DROP PLUGGABLE DATABASE {pdb} INCLUDING DATAFILES;

"""


def drop_pdb_sql(pdb: str, rac: bool) -> str:
    """One PDB per sqlplus session (the script batches them in one file; same statements)."""
    return "WHENEVER OSERROR EXIT FAILURE\nWHENEVER SQLERROR EXIT SQL.SQLCODE\n" + drop_pdb_block(pdb, rac) + "EXIT\n"


def soe_setup_sql(pdb: str, soe_password: str) -> str:
    """create_swingbench.sh step 1: create the SOE user if missing, or reset its password."""
    return f"""WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET DEFINE OFF
SET ECHO OFF
ALTER SESSION SET CONTAINER = {pdb};
GRANT DBA TO soe IDENTIFIED BY "{soe_password}";
EXIT SUCCESS
"""
