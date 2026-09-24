import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const adminLinks = [
  { to: "/admin", label: "Dashboard", end: true },
  { to: "/admin/hospitals", label: "Hospitals" },
  { to: "/admin/sources", label: "Sources" },
  { to: "/admin/monitoring", label: "Monitoring" },
  { to: "/admin/verification", label: "Verification" },
  { to: "/admin/reconciliation", label: "Reconciliation" },
  { to: "/admin/reports", label: "Reports" },
  { to: "/admin/quality", label: "Quality" },
  { to: "/admin/audit", label: "Audit" },
  { to: "/admin/users", label: "Users" },
  { to: "/admin/health", label: "Health" },
];

const userLinks = [
  { to: "/", label: "Home", end: true },
  { to: "/search", label: "Search" },
  { to: "/compare", label: "Compare" },
  { to: "/my-hospitals", label: "My hospitals" },
  { to: "/quality", label: "Quality" },
  { to: "/reports", label: "Reports" },
];

export function AdminLayout() {
  const { user, logout, isAdmin } = useAuth();
  return (
    <div data-testid="admin-layout" style={{ minHeight: "100vh", background: "#f9fafb" }}>
      <header
        style={{
          background: "#111827",
          color: "#f9fafb",
          padding: "12px 20px",
          display: "flex",
          gap: 16,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <strong data-testid="admin-title">MedCompare Admin</strong>
        <nav style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {adminLinks.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.end} style={{ color: "#e5e7eb", fontSize: 13 }}>
              {l.label}
            </NavLink>
          ))}
        </nav>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          <span data-testid="current-user" style={{ fontSize: 13 }}>
            {user?.username} ({user?.role})
          </span>
          {!isAdmin ? (
            <NavLink to="/" style={{ color: "#93c5fd", fontSize: 13 }}>
              User view
            </NavLink>
          ) : null}
          <button type="button" onClick={() => void logout()} data-testid="logout">
            Logout
          </button>
        </div>
      </header>
      <main style={{ padding: 20, maxWidth: 1200, margin: "0 auto" }}>
        <Outlet />
      </main>
    </div>
  );
}

export function UserLayout() {
  const { user, logout } = useAuth();
  return (
    <div data-testid="user-layout" style={{ minHeight: "100vh", background: "#f9fafb" }}>
      <header
        style={{
          background: "#1e3a5f",
          color: "#f9fafb",
          padding: "12px 20px",
          display: "flex",
          gap: 16,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <strong data-testid="user-title">MedCompare</strong>
        <nav style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {userLinks.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.end} style={{ color: "#dbeafe", fontSize: 13 }}>
              {l.label}
            </NavLink>
          ))}
        </nav>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          {user?.role === "admin" ? (
            <NavLink to="/admin" style={{ color: "#93c5fd", fontSize: 13 }}>
              Admin
            </NavLink>
          ) : null}
          <span data-testid="current-user" style={{ fontSize: 13 }}>
            {user?.username} ({user?.role})
          </span>
          <button type="button" onClick={() => void logout()} data-testid="logout">
            Logout
          </button>
        </div>
      </header>
      <main style={{ padding: 20, maxWidth: 1100, margin: "0 auto" }}>
        <Outlet />
      </main>
    </div>
  );
}
