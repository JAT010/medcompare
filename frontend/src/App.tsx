import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { AdminLayout, UserLayout } from "./layouts/Layouts";
import { LoginPage } from "./pages/LoginPage";
import {
  AdminAuditPage,
  AdminDashboardPage,
  AdminHealthPage,
  AdminHospitalsPage,
  AdminMonitoringPage,
  AdminQualityPage,
  AdminReconciliationPage,
  AdminReportsPage,
  AdminSourcesPage,
  AdminUsersPage,
  AdminVerificationPage,
} from "./pages/admin";
import {
  UserComparePage,
  UserHospitalDetailPage,
  UserHomePage,
  UserHospitalsPage,
  UserQualityPage,
  UserReportsPage,
  UserSearchPage,
} from "./pages/user";

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route element={<UserLayout />}>
              <Route path="/" element={<UserHomePage />} />
              <Route path="/search" element={<UserSearchPage />} />
              <Route path="/compare" element={<UserComparePage />} />
              <Route path="/my-hospitals" element={<UserHospitalsPage />} />
              <Route path="/hospitals/:hospitalId" element={<UserHospitalDetailPage />} />
              <Route path="/quality" element={<UserQualityPage />} />
              <Route path="/reports" element={<UserReportsPage />} />
            </Route>
          </Route>
          <Route element={<RequireAuth roles={["admin"]} />}>
            <Route element={<AdminLayout />}>
              <Route path="/admin" element={<AdminDashboardPage />} />
              <Route path="/admin/hospitals" element={<AdminHospitalsPage />} />
              <Route path="/admin/sources" element={<AdminSourcesPage />} />
              <Route path="/admin/monitoring" element={<AdminMonitoringPage />} />
              <Route path="/admin/verification" element={<AdminVerificationPage />} />
              <Route path="/admin/reconciliation" element={<AdminReconciliationPage />} />
              <Route path="/admin/reports" element={<AdminReportsPage />} />
              <Route path="/admin/quality" element={<AdminQualityPage />} />
              <Route path="/admin/audit" element={<AdminAuditPage />} />
              <Route path="/admin/users" element={<AdminUsersPage />} />
              <Route path="/admin/health" element={<AdminHealthPage />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
