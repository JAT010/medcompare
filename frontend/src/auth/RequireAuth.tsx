import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function RequireAuth({ roles }: { roles?: string[] }) {
  const { ready, user } = useAuth();
  const location = useLocation();
  if (!ready) return <div data-testid="auth-loading">Checking session…</div>;
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (roles && !roles.includes(user.role)) {
    return <Navigate to={user.role === "admin" ? "/admin" : "/"} replace />;
  }
  return <Outlet />;
}
