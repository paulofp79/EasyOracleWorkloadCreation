// Labels, grouping and hints for profile parameters (PROJECT_CONTEXT §5).
export interface FieldUi {
  label: string;
  group: string;
  hint?: string;
  unit?: string;
}

export const FIELD_UI: Record<string, FieldUi> = {
  prefix: { label: "PDB prefix", group: "Common", hint: "Names are PREFIX_001 … PREFIX_999" },
  scan_host: { label: "SCAN host", group: "Connection" },
  scan_port: { label: "SCAN port", group: "Connection" },
  service_suffix: { label: "Service suffix", group: "Connection", hint: "Appended to the lower-case PDB name, e.g. .us.oracle.com" },
  cdb_connect: { label: "CDB connection (SYSDBA)", group: "Connection", hint: "Remote: this app runs on a separate server" },
  cdb_service: { label: "CDB root service", group: "Connection", hint: "Remote mode: service of CDB$ROOT, e.g. cdb1.us.oracle.com" },
  rac_cdb: { label: "RAC CDB", group: "Create", hint: "OPEN/CLOSE … INSTANCES=ALL; off = OPEN + SAVE STATE" },
  create_first: { label: "First", group: "Create" },
  create_last: { label: "Last", group: "Create" },
  load_first: { label: "First", group: "Load SOE" },
  load_last: { label: "Last", group: "Load SOE" },
  scale: { label: "Scale", group: "Load SOE" },
  threads: { label: "Threads (-tc)", group: "Load SOE" },
  tablespace: { label: "Tablespace (-ts)", group: "Load SOE" },
  parallel: { label: "Parallel loaders", group: "Load SOE" },
  keep_going: { label: "Keep going after a failure", group: "Load SOE" },
  oewizard_path: { label: "oewizard path", group: "Load SOE" },
  check_timeout: { label: "Timeout", group: "Connection check", unit: "s" },
  check_parallel: { label: "Parallel checks", group: "Connection check" },
  skip_unreachable: { label: "Skip unreachable PDBs", group: "Connection check", hint: "Off = any failed check aborts the workload" },
  workload_first: { label: "First", group: "Workload" },
  workload_last: { label: "Last", group: "Workload" },
  users: { label: "Users per PDB (-uc)", group: "Workload" },
  runtime: { label: "Runtime (-rt)", group: "Workload", hint: "HH:MM" },
  charbench_path: { label: "charbench path", group: "Workload" },
  config_xml: { label: "Config XML (-c)", group: "Workload" },
  disable_txns: { label: "Disabled transactions (-di)", group: "Workload", hint: "Empty = none" },
  start_delay: { label: "Start delay", group: "Workload", unit: "s", hint: "Pause between launches" },
  log_dir: { label: "Log / run directory", group: "Storage" },
};

export const GROUPS = ["Common", "Connection", "Create", "Load SOE", "Connection check", "Workload", "Storage"];
