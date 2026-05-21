import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import AgentAdminPage from "./pages/AgentAdminPage";
import DashboardPage from "./pages/DashboardPage";
import LandingPage from "./pages/LandingPage";
import UploadPage from "./pages/UploadPage";

export default function App() {
  const location = useLocation();

  if (location.pathname === "/") {
    return <LandingPage />;
  }

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
            <nav className="flex gap-3">
              <NavLink
                to="/upload"
                className={({ isActive }) =>
                  `rounded-full px-4 py-2 text-sm font-medium transition ${
                    isActive ? "bg-white text-brand-800" : "bg-white/10 text-white hover:bg-white/20"
                  }`
                }
              >
                Upload
              </NavLink>
              <NavLink
                to="/dashboard"
                className={({ isActive }) =>
                  `rounded-full px-4 py-2 text-sm font-medium transition ${
                    isActive ? "bg-white text-brand-800" : "bg-white/10 text-white hover:bg-white/20"
                  }`
                }
              >
                Dashboard
              </NavLink>
              <NavLink
                to="/agent"
                className={({ isActive }) =>
                  `rounded-full px-4 py-2 text-sm font-medium transition ${
                    isActive ? "bg-white text-brand-800" : "bg-white/10 text-white hover:bg-white/20"
                  }`
                }
              >
                Agent Admin
              </NavLink>
            </nav>
          </div>
        </header>

        <main className="flex-1">
          <Routes>
            <Route path="/upload" element={<UploadPage />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/agent" element={<AgentAdminPage />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
