import { FormEvent, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ErrorState } from "../components/ui";

export function LoginPage() {
  const { login, user, ready } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (ready && user) {
    const dest = (location.state as { from?: string } | null)?.from;
    return <Navigate to={dest || (user.role === "admin" ? "/admin" : "/")} replace />;
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await login(username.trim(), password);
      const dest = (location.state as { from?: string } | null)?.from;
      if (dest) navigate(dest, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      data-testid="login-page"
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        background: "#f3f4f6",
        padding: 16,
      }}
    >
      <form
        onSubmit={onSubmit}
        style={{
          width: "min(400px, 100%)",
          background: "#fff",
          border: "1px solid #e5e7eb",
          borderRadius: 10,
          padding: 24,
          display: "grid",
          gap: 12,
        }}
      >
        <h1 style={{ margin: 0, fontSize: 20 }}>MedCompare sign in</h1>
        <p style={{ margin: 0, fontSize: 13, color: "#6b7280" }}>
          Demo users: admin@medcompare.demo / user@medcompare.demo. Passwords come from
          server environment variables — never from this frontend.
        </p>
        {error ? <ErrorState message={error} /> : null}
        <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
          Username
          <input
            data-testid="username"
            name="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
          Password
          <input
            data-testid="password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <button type="submit" data-testid="login-submit" disabled={loading}>
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
