"""Golden tests: SQL the app sends must equal the heredocs in reference/*.sh (after variable substitution)."""
from app.config import REPO_ROOT
from app.parsing import CheckResult, first_error, parse_check, parse_inventory, parse_preflight
from app.sqlbuild import check_sql, inventory_sql, preflight_sql

REF = REPO_ROOT / "reference"


def heredoc(script: str, opener_contains: str) -> str:
    """Body of the <<SQL heredoc whose opening line contains opener_contains, as bash would expand it."""
    lines = (REF / script).read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if opener_contains in ln and ln.rstrip().endswith("<<SQL"))
    body = []
    for ln in lines[start + 1:]:
        if ln == "SQL":
            break
        body.append(ln)
    return "\n".join(body).replace("\\$", "$") + "\n"


def test_preflight_matches_create_swingbench():
    expected = heredoc("create_swingbench.sh", "preflight_raw=").replace("${PDB_PREFIX}", "PDBPP")
    assert preflight_sql("PDBPP") == expected


def test_check_matches_run_charbench():
    expected = (heredoc("run_charbench_all_pdbs.sh", "sqlplus -L -s /nolog")
                .replace("${SOE_PASSWORD}", "Pw_1").replace("${service}", "//scan:1521/pdbpp_001.us"))
    assert check_sql("Pw_1", "//scan:1521/pdbpp_001.us") == expected


def test_inventory_queries_by_prefix_without_in_list():
    sql = inventory_sql("PDBPP")
    assert "LIKE 'PDBPP%'" in sql and " IN (" not in sql
    assert max(len(ln) for ln in sql.splitlines()) < 2500   # SQL*Plus line limit (gotcha 3)


def test_parse_check_ok():
    out = "SOE_CHECK_OK|PDBPP_001 |cdb1|4\n"
    assert parse_check("PDBPP_001", out, 0, False, 30) == CheckResult("OK", "cdb1", "cdb1")


def test_parse_check_container_case_insensitive():
    assert parse_check("PDBPP_001", "SOE_CHECK_OK|pdbpp_001|i2|4", 0, False, 30).status == "OK"


def test_parse_check_reasons():
    ora = "ERROR:\nORA-12514: TNS:listener does not currently know of service requested in connect descriptor\n\n\nSP2-0640: Not connected\n"
    assert parse_check("PDBPP_003", ora, 0, False, 30).detail.startswith("ORA-12514: TNS:listener")
    assert parse_check("PDBPP_003", ora, 0, False, 30).status == "FAIL"
    assert parse_check("X_001", "SOE_CHECK_OK|CDB$ROOT|i1|4", 0, False, 30).detail == \
        "connected to container CDB$ROOT, expected X_001"
    assert parse_check("X_001", "SOE_CHECK_OK|X_001|i1|0", 0, False, 30).detail == \
        "SOE schema missing or incomplete (0/4 core tables)"
    assert parse_check("X_001", "", None, True, 30).detail == "timed out after 30s"
    assert parse_check("X_001", "partial", 137, False, 7).detail == "timed out after 7s"
    assert parse_check("X_001", "", 1, False, 30).detail == "no output from sqlplus (exit code 1)"
    assert parse_check("X_001", "\n  weird thing\n", 0, False, 30).detail == "weird thing"
    assert len(parse_check("X_001", "ORA-1" + "x" * 500, 0, False, 30).detail) == 200
    assert "\t" not in parse_check("X_001", "ORA-1\tbad", 0, False, 30).detail


def test_first_error_prefers_ora_line():
    assert first_error("hello\nSP2-0306: Invalid option.\n") == "SP2-0306: Invalid option."


def test_parse_inventory():
    out = ("PDB|PDBPP_001|111|1|READ WRITE|cdb1|NO\nPDB|PDBPP_001|111|2|MOUNTED|cdb2|NO\n"
           "garbage\nPDB|PDBPP_002|222|1|READ WRITE|cdb1|YES\n")
    inv = parse_inventory(out)
    assert inv["PDBPP_001"].rw_instances == 1 and len(inv["PDBPP_001"].instances) == 2
    assert inv["PDBPP_002"].restricted


def test_parse_preflight():
    assert parse_preflight("PDBPP_001|123|2\n bad line\nPDBPP_002 | 456 | 0\n") == \
        {"PDBPP_001": ("123", 2), "PDBPP_002": ("456", 0)}


# ---- create / drop (create_pdbs.sh) ----------------------------------------------------------------
import re  # noqa: E402

import pytest  # noqa: E402

from app.sqlbuild import create_pdb_sql, drop_pdb_block, drop_pdb_sql  # noqa: E402

CREATE_SH = (REF / "create_pdbs.sh").read_text()


def script_assignment(name: str, branch: int) -> str:
    """Value of open_sql/save_sql in the RAC (0) or non-RAC (1) branch, as bash expands it."""
    vals = re.findall(rf'^\s*{name}="(.*)"$', CREATE_SH, re.M)
    return vals[branch]


def script_tde() -> str:
    m = re.search(r'tde_sql="(ALTER SESSION.*?CURRENT;)"', CREATE_SH, re.S)
    return m.group(1).replace('\\"', '"')


def nth_heredoc(script_text: str, opener: str, n: int) -> str:
    lines = script_text.splitlines()
    starts = [i for i, ln in enumerate(lines) if opener in ln and ln.rstrip().endswith("<<SQL")]
    body = []
    for ln in lines[starts[n] + 1:]:
        if ln == "SQL":
            break
        body.append(ln)
    return "\n".join(body).replace("\\$", "$") + "\n"


def expected_create(pdb: str, admin: str, rac: bool, wallet: str | None) -> str:
    body = nth_heredoc(CREATE_SH, 'sqlplus -L -s / as sysdba >"$sql_log"', 0)
    branch = 0 if rac else 1
    tde = script_tde().replace("${TDE_WALLET_PASSWORD}", wallet) if wallet is not None else ""
    return (body.replace("${open_sql}", script_assignment("open_sql", branch))
                .replace("${save_sql}", script_assignment("save_sql", branch))
                .replace("${tde_sql}", tde)
                .replace("${PDB_ADMIN_PASSWORD}", admin)
                .replace("${pdb}", pdb))


@pytest.mark.parametrize("rac", [True, False])
@pytest.mark.parametrize("wallet", [None, "W@llet-1"])
def test_create_sql_matches_create_pdbs(rac, wallet):
    assert create_pdb_sql("PDBPP_007", "Adm1n'pw", rac, wallet) == expected_create("PDBPP_007", "Adm1n'pw", rac, wallet)


def test_wallet_off_has_no_tde_statements():
    sql = create_pdb_sql("PDBPP_001", "x", True, None)
    assert "ADMINISTER" not in sql and "SET CONTAINER" not in sql and "KEYSTORE" not in sql


def test_rac_vs_single_instance_open():
    assert "OPEN INSTANCES=ALL;" in create_pdb_sql("P_001", "x", True, None)
    si = create_pdb_sql("P_001", "x", False, None)
    assert "ALTER PLUGGABLE DATABASE P_001 OPEN;" in si and "SAVE STATE;" in si and "INSTANCES" not in si


@pytest.mark.parametrize("rac,n", [(True, 0), (False, 1)])
def test_drop_block_matches_create_pdbs(rac, n):
    expected = nth_heredoc(CREATE_SH, "cat <<SQL", n).replace("${pdb}", "PDBPP_042")
    expected += "DROP PLUGGABLE DATABASE PDBPP_042 INCLUDING DATAFILES;\n\n"
    assert drop_pdb_block("PDBPP_042", rac) == expected


def test_drop_sql_has_script_header_and_exit():
    sql = drop_pdb_sql("PDBPP_042", True)
    assert sql.startswith("WHENEVER OSERROR EXIT FAILURE\nWHENEVER SQLERROR EXIT SQL.SQLCODE\nDECLARE")
    assert sql.endswith("INCLUDING DATAFILES;\n\nEXIT\n")


# ---- load SOE (create_swingbench.sh) -------------------------------------------------------------
import shlex  # noqa: E402

from app.cmdbuild import display, oewizard_argv  # noqa: E402
from app.sqlbuild import soe_setup_sql  # noqa: E402

LOAD_SH = (REF / "create_swingbench.sh").read_text()


def test_soe_setup_matches_create_swingbench():
    expected = nth_heredoc(LOAD_SH, 'sqlplus -L -s / as sysdba >"$sql_log"', 0)
    expected = expected.replace("${pdb}", "PDBPP_222").replace("${SOE_PASSWORD}", "s0e'pw")
    assert soe_setup_sql("PDBPP_222", "s0e'pw") == expected


def test_oewizard_argv_matches_create_swingbench():
    m = re.search(r'if ! "\$OEWIZARD" \\\n(.*?)>"\$wizard_log"', LOAD_SH, re.S)
    text = m.group(1).replace("\\\n", " ")
    subst = {"$service": "//scan:1521/pdbpp_222", "$SOE_PASSWORD": "pw1", "$SCALE": "0.1", "$THREADS": "16",
             "$DBA_PASSWORD": "pw2", "$TABLESPACE": "SOETBS"}
    expected = ["/sb/bin/oewizard"]
    for tok in shlex.split(text):
        expected.append(subst.get(tok, tok))
    got = oewizard_argv("/sb/bin/oewizard", "//scan:1521/pdbpp_222", "pw1", "0.1", 16, "pw2", "SOETBS")
    assert got == expected


def test_display_masks_both_passwords():
    shown = display(oewizard_argv("/x/oewizard", "//h:1/s", "pw1", "1", 4, "pw2", "T"))
    assert "pw1" not in shown and "pw2" not in shown and shown.count("'********'") == 2
    assert "-dba 'sys as sysdba'" in shown
