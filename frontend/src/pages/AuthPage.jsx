import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";


const roleOptions = ["teacher", "student", "parent", "admin"];


export default function AuthPage({ mode = "login" }) {
  const { user, login, signup } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [currentMode, setCurrentMode] = useState(mode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState("teacher");
  const [tenantKey, setTenantKey] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  if (user) {
    const destination = location.state?.from?.pathname || "/dashboard";
    return <Navigate to={destination} replace />;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setLoading(true);

    try {
      if (currentMode === "signup") {
        await signup({ email, password, display_name: displayName, role, tenant_key: tenantKey });
      } else {
        await login(email, password);
      }
      navigate(location.state?.from?.pathname || "/dashboard", { replace: true });
    } catch (submitError) {
      setError(submitError?.response?.data?.detail || "Authentication failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,rgba(38,141,130,0.12),transparent_26%),radial-gradient(circle_at_top_right,rgba(14,165,233,0.10),transparent_22%),linear-gradient(180deg,#eef6f4_0%,#f8fafc_42%,#eef6f4_100%)] text-slate-900">
      <div className="mx-auto flex min-h-screen max-w-7xl items-center px-4 py-8 sm:px-6 lg:px-8">
        <div className="grid w-full gap-8 lg:grid-cols-[0.9fr_1.1fr]">
          <section className="rounded-[2rem] border border-white/70 bg-white/85 p-8 shadow-sm backdrop-blur">
            <p className="text-sm uppercase tracking-[0.3em] text-brand-700">Acadence AI</p>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">Sign in to the multi-tenant workspace.</h1>
            <p className="mt-4 text-sm leading-7 text-slate-600">
              Access authenticated dashboards, tenant-scoped uploads, and user-specific analytics with the same interface language already present in the app.
            </p>
            <div className="mt-8 grid gap-3 sm:grid-cols-2">
              <div className="rounded-2xl bg-slate-50 p-4">
                <div className="text-xs uppercase tracking-[0.25em] text-slate-500">Auth</div>
                <div className="mt-2 text-sm font-semibold text-slate-950">JWT + refresh tokens</div>
              </div>
              <div className="rounded-2xl bg-slate-50 p-4">
                <div className="text-xs uppercase tracking-[0.25em] text-slate-500">Isolation</div>
                <div className="mt-2 text-sm font-semibold text-slate-950">User-owned datasets</div>
              </div>
              <div className="rounded-2xl bg-slate-50 p-4">
                <div className="text-xs uppercase tracking-[0.25em] text-slate-500">Retrieval</div>
                <div className="mt-2 text-sm font-semibold text-slate-950">pgvector-backed search</div>
              </div>
              <div className="rounded-2xl bg-slate-50 p-4">
                <div className="text-xs uppercase tracking-[0.25em] text-slate-500">Roles</div>
                <div className="mt-2 text-sm font-semibold text-slate-950">Teacher, student, parent, admin</div>
              </div>
            </div>
          </section>

          <section className="rounded-[2rem] border border-white/70 bg-white/90 p-8 shadow-soft backdrop-blur">
            <div className="flex gap-2 rounded-full bg-slate-100 p-1">
              <button
                type="button"
                onClick={() => setCurrentMode("login")}
                className={`flex-1 rounded-full px-4 py-2 text-sm font-medium transition ${currentMode === "login" ? "bg-brand-700 text-white" : "text-slate-600"}`}
              >
                Login
              </button>
              <button
                type="button"
                onClick={() => setCurrentMode("signup")}
                className={`flex-1 rounded-full px-4 py-2 text-sm font-medium transition ${currentMode === "signup" ? "bg-brand-700 text-white" : "text-slate-600"}`}
              >
                Sign up
              </button>
            </div>

            <form className="mt-8 space-y-4" onSubmit={handleSubmit}>
              <label className="block space-y-2">
                <span className="text-sm font-medium text-slate-700">Email</span>
                <input
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  type="email"
                  required
                  className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none transition focus:border-brand-300 focus:ring-2 focus:ring-brand-100"
                  placeholder="you@example.com"
                />
              </label>

              <label className="block space-y-2">
                <span className="text-sm font-medium text-slate-700">Password</span>
                <input
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  type="password"
                  required
                  className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none transition focus:border-brand-300 focus:ring-2 focus:ring-brand-100"
                  placeholder="••••••••"
                />
              </label>

              {currentMode === "signup" ? (
                <>
                  <label className="block space-y-2">
                    <span className="text-sm font-medium text-slate-700">Display name</span>
                    <input
                      value={displayName}
                      onChange={(event) => setDisplayName(event.target.value)}
                      type="text"
                      required
                      className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none transition focus:border-brand-300 focus:ring-2 focus:ring-brand-100"
                      placeholder="Platform Admin"
                    />
                  </label>

                  <div className="grid gap-4 sm:grid-cols-2">
                    <label className="block space-y-2">
                      <span className="text-sm font-medium text-slate-700">Role</span>
                      <select
                        value={role}
                        onChange={(event) => setRole(event.target.value)}
                        className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none transition focus:border-brand-300 focus:ring-2 focus:ring-brand-100"
                      >
                        {roleOptions.map((item) => (
                          <option key={item} value={item}>
                            {item}
                          </option>
                        ))}
                      </select>
                    </label>

                    <label className="block space-y-2">
                      <span className="text-sm font-medium text-slate-700">Tenant key</span>
                      <input
                        value={tenantKey}
                        onChange={(event) => setTenantKey(event.target.value)}
                        type="text"
                        className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none transition focus:border-brand-300 focus:ring-2 focus:ring-brand-100"
                        placeholder="school-a"
                      />
                    </label>
                  </div>
                </>
              ) : null}

              {error ? <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div> : null}

              <button
                type="submit"
                disabled={loading}
                className="inline-flex w-full items-center justify-center rounded-full bg-brand-700 px-6 py-3 text-sm font-semibold text-white shadow-soft transition hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {loading ? "Please wait..." : currentMode === "signup" ? "Create account" : "Login"}
              </button>
            </form>

            <p className="mt-6 text-center text-sm text-slate-500">
              <Link to="/" className="font-medium text-brand-700 transition hover:text-brand-600">
                Back to landing page
              </Link>
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}