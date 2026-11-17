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
