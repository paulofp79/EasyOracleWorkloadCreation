import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api/client";
import Check from "./pages/Check";
import Dashboard from "./pages/Dashboard";
import Environment from "./pages/Environment";
import Placeholder from "./pages/Placeholder";
import Pdbs from "./pages/Pdbs";
import Profiles from "./pages/Profiles";
import RunDetail from "./pages/RunDetail";
import Runs from "./pages/Runs";

const NAV: { to: string; label: string; milestone?: string }[] = [
  { to: "/", label: "Dashboard" },
  { to: "/pdbs", label: "PDBs" },
  { to: "/create", label: "Create PDBs", milestone: "M2" },
  { to: "/load", label: "Load SOE", milestone: "M3" },
  { to: "/check", label: "Check connections" },
  { to: "/workload", label: "Run workload", milestone: "M4" },
  { to: "/runs", label: "Runs" },
  { to: "/profiles", label: "Profiles" },
  { to: "/environment", label: "Environment" },
];

type Theme = "auto" | "light" | "dark";

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("theme") as Theme) || "auto";
    } catch {
      return "auto";
    }
  });
  useEffect(() => {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* storage unavailable */
    }
  }, [theme]);
  return [theme, setTheme];
}

function ProfileSwitcher() {
  const qc = useQueryClient();
  const { data: profiles } = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const activate = useMutation({
    mutationFn: (id: number) => api.activateProfile(id),
    onSuccess: () => qc.invalidateQueries(),
  });
  const active = profiles?.find((p) => p.is_active);
  return (
    <label className="profile-switch">
      <span>Profile</span>
      <select value={active?.id ?? ""} onChange={(e) => activate.mutate(Number(e.target.value))} disabled={!profiles}>
        {profiles?.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function App() {
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health });
  const [theme, setTheme] = useTheme();
  const location = useLocation();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          PDB Orchestrator
          <small>Swingbench SOE</small>
        </div>
        <nav>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
              <span>{n.label}</span>
              {n.milestone && <span className="tag" title={`Arrives in milestone ${n.milestone}`}>{n.milestone}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">v{health?.version ?? "…"}</div>
      </aside>
      <div className="main">
        <header className="topbar">
          {health?.mode === "simulate" && (
            <span className="badge badge-sim" title="Stub executables from tools/stubs are used instead of Oracle tools">
              SIMULATION
            </span>
          )}
          {health?.mode === "real" && <span className="badge badge-real">LIVE ORACLE</span>}
          <div className="spacer" />
          <ProfileSwitcher />
          <label className="theme-switch">
            <span>Theme</span>
            <select value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
              <option value="auto">Auto</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </label>
        </header>
        <main className="content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/profiles" element={<Profiles />} />
            <Route path="/environment" element={<Environment />} />
            <Route path="/pdbs" element={<Pdbs />} />
            <Route path="/check" element={<Check />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:id" element={<RunDetail key={location.pathname} />} />
            {NAV.filter((n) => n.milestone).map((n) => (
              <Route key={n.to} path={n.to} element={<Placeholder title={n.label} milestone={n.milestone!} />} />
            ))}
            <Route path="*" element={<Placeholder title="Not found" />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
