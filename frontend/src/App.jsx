import { NavLink, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { useAuth, AuthProvider } from "./context/AuthContext";
import ProtectedRoute from "./components/ProtectedRoute";
import AuthPage from "./pages/AuthPage";
import AgentAdminPage from "./pages/AgentAdminPage";
import DashboardPage from "./pages/DashboardPage";
import LandingPage from "./pages/LandingPage";
import UploadPage from "./pages/UploadPage";


function AppShell() {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <div className="mx-auto flex min-h-screen max-w-7xl flex-col px-4 py-6 sm:px-6 lg:px-8">
        <header className="mb-6 rounded-3xl bg-gradient-to-r from-brand-900 via-brand-700 to-brand-500 p-6 text-white shadow-soft">
          <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <p className="text-sm uppercase tracking-[0.3em] text-brand-100">Academic Intelligence</p>
              <h1 className="mt-2 text-3xl font-semibold">Student Result Analytics</h1>
              <p className="mt-2 max-w-2xl text-sm text-brand-50">
                Upload Excel or PDF results, normalize them, analyze SGPA and pass status, and explore the processed dataset.
              </p>
            </div>
            <div className="flex flex-col gap-3 md:items-end">
              <div className="rounded-full bg-white/10 px-4 py-2 text-sm text-white">
                {user?.display_name || user?.email} · {user?.role}
              </div>
              <nav className="flex flex-wrap gap-3">
                <NavLink to="/upload" className={({ isActive }) => `rounded-full px-4 py-2 text-sm font-medium transition ${isActive ? "bg-white text-brand-800" : "bg-white/10 text-white hover:bg-white/20"}`}>
                  Upload
                </NavLink>
                <NavLink to="/dashboard" className={({ isActive }) => `rounded-full px-4 py-2 text-sm font-medium transition ${isActive ? "bg-white text-brand-800" : "bg-white/10 text-white hover:bg-white/20"}`}>
                  Dashboard
                </NavLink>
                <NavLink to="/agent" className={({ isActive }) => `rounded-full px-4 py-2 text-sm font-medium transition ${isActive ? "bg-white text-brand-800" : "bg-white/10 text-white hover:bg-white/20"}`}>
                  Agent Admin
                </NavLink>
                <button onClick={logout} className="rounded-full bg-slate-950/20 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-950/30">
                  Logout
                </button>
              </nav>
            </div>
          </div>
        </header>

        <main className="flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const location = useLocation();

  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<AuthPage mode="login" />} />
        <Route path="/signup" element={<AuthPage mode="signup" />} />
        <Route
          element={
            <ProtectedRoute>
              <AppShell />
            </ProtectedRoute>
          }
        >
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/agent" element={<AgentAdminPage />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}
