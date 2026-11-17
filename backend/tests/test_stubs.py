"""The simulation stubs are part of the test infrastructure; make sure they emulate the tools faithfully."""
import json
import subprocess
import time
from pathlib import Path

from app.config import STUB_DIR
from app.engine.runner import ProcessRunner
from app.sqlbuild import env_check_sql

from .conftest import pid_gone, run_stub

SYSDBA = ["-L", "-s", "/", "as", "sysdba"]
INVENTORY = """SET PAGESIZE 0
SELECT name || '|' || con_uid || '|' || SUM(CASE WHEN open_mode = 'READ WRITE' THEN 1 ELSE 0 END)
FROM   gv$pdbs
WHERE  name LIKE 'PDBPP%'
GROUP  BY name, con_uid
ORDER  BY name;
EXIT
"""


def check_sql(pdb_service: str) -> str:
    return f"""WHENEVER SQLERROR EXIT FAILURE
CONNECT soe/"pw"@//scan:1521/{pdb_service}
SELECT 'SOE_CHECK_OK|' FROM dual;
EXIT
"""


def inventory(env):
    out = run_stub(env, "sqlplus", SYSDBA, INVENTORY).stdout.split()
    return {ln.split("|")[0]: ln.split("|") for ln in out}


def test_env_check_query(stub_env):
    r = run_stub(stub_env, "sqlplus", SYSDBA, env_check_sql())
    assert r.returncode == 0
    assert r.stdout.strip() == "ENV_CHECK|SIMCDB|19.0.0.0.0|2|YES|CDB$ROOT|TRUE"


def test_inventory(stub_env):
    inv = inventory(stub_env)
    assert len(inv) == 40
    assert inv["PDBPP_001"][2] == "2"
    assert inv["PDBPP_029"][2] == "0"        # scenario: mounted


def test_connection_check_scenarios(stub_env):
    def check(svc):
        return run_stub(stub_env, "sqlplus", ["-L", "-s", "/nolog"], check_sql(svc))

    ok = check("pdbpp_001.us.oracle.com")
    assert ok.returncode == 0 and ok.stdout.strip().startswith("SOE_CHECK_OK|PDBPP_001|simcdb")
    assert ok.stdout.strip().endswith("|4")
    bad = check("pdbpp_003")
    assert "ORA-12514" in bad.stdout and "SP2-0640" in bad.stdout
    assert bad.returncode == 0               # gotcha 1: exit code does not tell
    assert "SOE_CHECK_OK|CDB$ROOT|" in check("pdbpp_011").stdout
    assert check("pdbpp_013").stdout.strip().endswith("|0")
    assert "ORA-12514" in check("pdbpp_029").stdout    # mounted -> service not registered
    assert "ORA-12514" in check("pdbpp_777").stdout    # does not exist


def test_hang_scenario_is_killed_by_timeout(stub_env):
    import asyncio
    r = asyncio.run(ProcessRunner(stub_env).run(["sqlplus", "-L", "-s", "/nolog"],
                                                stdin_text=check_sql("pdbpp_007"), timeout=1, timeout_grace=1))
    assert r.timed_out


def test_create_open_drop_cycle(stub_env):
    create = 'WHENEVER SQLERROR EXIT SQL.SQLCODE\nCREATE PLUGGABLE DATABASE PDBPP_041\n  ADMIN USER pdbadmin ' \
             'IDENTIFIED BY "x";\nALTER PLUGGABLE DATABASE PDBPP_041 OPEN INSTANCES=ALL;\nALTER SYSTEM REGISTER;\nEXIT\n'
    r = run_stub(stub_env, "sqlplus", SYSDBA, create)
    assert r.returncode == 0, r.stdout
    assert inventory(stub_env)["PDBPP_041"][2] == "2"
    again = run_stub(stub_env, "sqlplus", SYSDBA, create)
    assert "ORA-65012" in again.stdout and again.returncode == 65012 % 256
    drop = ("WHENEVER SQLERROR EXIT SQL.SQLCODE\nDECLARE\n  n PLS_INTEGER;\nBEGIN\n  SELECT COUNT(*) INTO n FROM gv$pdbs\n"
            "  WHERE  name = 'PDBPP_041'\n  AND    open_mode <> 'MOUNTED';\n  IF n > 0 THEN\n    EXECUTE IMMEDIATE\n"
            "      'ALTER PLUGGABLE DATABASE PDBPP_041 CLOSE IMMEDIATE INSTANCES=ALL';\n  END IF;\nEND;\n/\n\n"
            "DROP PLUGGABLE DATABASE PDBPP_041 INCLUDING DATAFILES;\nEXIT\n")
    r = run_stub(stub_env, "sqlplus", SYSDBA, drop)
    assert r.returncode == 0, r.stdout
    assert "PDBPP_041" not in inventory(stub_env)


def test_create_fail_scenario(stub_env):
    r = run_stub(stub_env, "sqlplus", SYSDBA,
                 'WHENEVER SQLERROR EXIT SQL.SQLCODE\nCREATE PLUGGABLE DATABASE PDBPP_031\n  ADMIN USER a IDENTIFIED BY "x";\n')
    assert r.returncode != 0 and "ORA-01119" in r.stdout


def test_oewizard_success_and_failure(stub_env):
    args = ["-cl", "-create", "-u", "soe", "-p", "pw", "-scale", "0.1", "-tc", "4",
            "-dba", "sys as sysdba", "-dbap", "syspw", "-ts", "SOETBS", "-async_off", "-v"]
    ok = run_stub(stub_env, "oewizard", ["-cs", "//scan:1521/pdbpp_002", *args])
    assert ok.returncode == 0 and "Schema Created" in ok.stdout
    fail = run_stub(stub_env, "oewizard", ["-cs", "//scan:1521/pdbpp_017", *args])
    assert fail.returncode == 1 and "ORA-01652" in fail.stdout


def test_charbench_records_args_and_child_dies_with_group(stub_env, sim_settings, tmp_path, reap_strays):
    import os
    import signal
    results = tmp_path / "PDBPP_005-results.xml"
    p = subprocess.Popen([str(STUB_DIR / "charbench"), "-cs", "//scan:1521/pdbpp_005", "-u", "soe", "-p", "TopSecret",
                          "-uc", "4", "-rt", "10:00", "-r", str(results)],
                         env=stub_env, stdout=subprocess.DEVNULL, start_new_session=True)
    rec_dir = Path(sim_settings.sim_state).parent / "charbench-args"
    for _ in range(100):
        if (rec_dir / "PDBPP_005.child").exists():
            break
        time.sleep(0.05)
    child = int((rec_dir / "PDBPP_005.child").read_text())
    reap_strays.append(child)
    rec = json.loads((rec_dir / "PDBPP_005.json").read_text())
    assert "TopSecret" not in json.dumps(rec)
    assert rec["argv"][rec["argv"].index("-uc") + 1] == "4"
    assert os.getpgid(child) == p.pid
    os.killpg(p.pid, signal.SIGTERM)
    p.wait(5)
    time.sleep(0.1)
    assert pid_gone(child)


def test_charbench_short_run_writes_results_and_errors(stub_env, tmp_path):
    results = tmp_path / "r.xml"
    r = run_stub(stub_env, "charbench", ["-cs", "//scan:1521/pdbpp_019", "-u", "soe", "-p", "x", "-uc", "1",
                                         "-rt", "00:00:04", "-r", str(results)])
    assert r.returncode == 0
    assert results.read_text().startswith("<?xml")
    r = run_stub(stub_env, "charbench", ["-cs", "//scan:1521/pdbpp_023", "-u", "soe", "-p", "x", "-uc", "1",
                                         "-rt", "00:00:01", "-r", str(results)])
    assert r.returncode == 1
