"""SQL text sent to sqlplus on stdin. Only whitelisted operations; inputs must be validated first.

M0 contains the environment check. Create/drop/inventory/grant/check builders arrive with
their milestones and are pinned by golden tests against reference/*.sh.
"""
from __future__ import annotations

ENV_CHECK_TOKEN = "ENV_CHECK|"

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
