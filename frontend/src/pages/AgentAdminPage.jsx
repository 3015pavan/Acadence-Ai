import { useEffect, useState } from "react";
import api from "../api";

function StatusPill({ running, status }) {
  const tone = running
    ? "bg-emerald-100 text-emerald-800 border-emerald-200"
    : status === "error"
      ? "bg-rose-100 text-rose-800 border-rose-200"
      : "bg-slate-100 text-slate-700 border-slate-200";

  return <span className={`inline-flex rounded-full border px-3 py-1 text-sm font-semibold ${tone}`}>{running ? "Running" : status}</span>;
}

function formatTimestamp(value) {
  if (!value) {
    return "Not yet";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export default function AgentAdminPage() {
  const [status, setStatus] = useState(null);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState("");
  const [connectLoading, setConnectLoading] = useState(false);
  const [error, setError] = useState("");

  const loadAgentData = async () => {
    try {
      const [statusResponse, logsResponse] = await Promise.all([
        api.get("/agent/status"),
        api.get("/agent/logs", { params: { limit: 100 } }),
      ]);
      setStatus(statusResponse.data);
      setLogs(Array.isArray(logsResponse.data.logs) ? logsResponse.data.logs : []);
      setError("");
    } catch (requestError) {
      setError(requestError.response?.data?.detail || "Unable to load agent status.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAgentData();
    const intervalId = window.setInterval(loadAgentData, 10000);
    return () => window.clearInterval(intervalId);
  }, []);

  const runAction = async (path, loadingKey) => {
    setActionLoading(loadingKey);
    setError("");
    try {
      await api.post(path);
      await loadAgentData();
    } catch (requestError) {
      setError(requestError.response?.data?.detail || "Agent action failed.");
    } finally {
      setActionLoading("");
    }
  };

  const connectGmail = async () => {
    setConnectLoading(true);
    setError("");
    try {
      const response = await api.get("/agent/gmail/connect-url");
      const authorizationUrl = response.data.authorization_url;
      if (!authorizationUrl) {
        throw new Error("Missing Gmail authorization URL.");
      }
      window.open(authorizationUrl, "_blank", "noopener,noreferrer");
    } catch (requestError) {
      setError(requestError.response?.data?.detail || requestError.message || "Unable to start Gmail connection.");
    } finally {
      setConnectLoading(false);
    }
  };

  const disconnectGmail = async () => {
    setActionLoading("disconnect");
    setError("");
    try {
      await api.post("/agent/gmail/disconnect");
      await loadAgentData();
    } catch (requestError) {
      setError(requestError.response?.data?.detail || "Unable to disconnect Gmail.");
    } finally {
      setActionLoading("");
    }
  };

  if (loading) {
    return <div className="rounded-3xl bg-white p-10 text-center text-slate-600 shadow-soft">Loading agent controls...</div>;
  }

  if (!status) {
    return <div className="rounded-3xl bg-rose-50 p-10 text-center text-rose-700 shadow-soft">Agent status is unavailable.</div>;
  }

  return (
    <section className="space-y-6">
      <div className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.3em] text-teal-700">Email Agent</p>
            <h2 className="mt-2 text-2xl font-semibold text-slate-900">Autonomous inbox processing</h2>
            <p className="mt-2 max-w-3xl text-sm text-slate-600">
              This agent polls unread result emails, validates attachments, reuses the existing parse and indexing pipeline, generates a report, and replies to the sender with results.
            </p>
          </div>
          <StatusPill running={status.running} status={status.status} />
        </div>

        <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-3xl bg-slate-50 p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Polling</p>
            <p className="mt-2 text-2xl font-semibold text-slate-900">Every {status.interval_minutes} min</p>
            <p className="mt-1 text-sm text-slate-600">Configured with `AGENT_POLL_MINUTES`</p>
          </div>
          <div className="rounded-3xl bg-slate-50 p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Processed</p>
            <p className="mt-2 text-2xl font-semibold text-slate-900">{status.processed_emails_total}</p>
            <p className="mt-1 text-sm text-slate-600">Successful email runs</p>
          </div>
          <div className="rounded-3xl bg-slate-50 p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Failures</p>
            <p className="mt-2 text-2xl font-semibold text-slate-900">{status.failed_emails_total}</p>
            <p className="mt-1 text-sm text-slate-600">Logged processing failures</p>
          </div>
          <div className="rounded-3xl bg-slate-50 p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Last Email</p>
            <p className="mt-2 text-sm font-semibold text-slate-900">{status.last_processed_email || "No email processed yet"}</p>
            <p className="mt-1 text-sm text-slate-600">Most recent successful sender and subject</p>
          </div>
        </div>

        <div className="mt-6 rounded-3xl border border-dashed border-slate-200 bg-slate-50 p-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <p className="text-sm font-semibold text-slate-900">Gmail connection</p>
              <p className="mt-1 text-sm text-slate-600">
                {status.connected ? `Connected to ${status.connected_email || "a Gmail account"}.` : "Connect a Gmail account to enable inbox polling and automatic replies."}
              </p>
            </div>
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={connectGmail}
                disabled={connectLoading || actionLoading !== ""}
                className="rounded-full bg-sky-600 px-5 py-3 text-sm font-semibold text-white transition hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {connectLoading ? "Opening Gmail..." : "Connect Gmail"}
              </button>
              <button
                type="button"
                onClick={disconnectGmail}
                disabled={!status.connected || actionLoading !== ""}
                className="rounded-full border border-slate-300 px-5 py-3 text-sm font-semibold text-slate-700 transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-60"
              >
                {actionLoading === "disconnect" ? "Disconnecting..." : "Disconnect"}
              </button>
            </div>
          </div>
        </div>

        <div className="mt-6 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => runAction("/agent/start", "start")}
            disabled={actionLoading !== ""}
            className="rounded-full bg-emerald-600 px-5 py-3 text-sm font-semibold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {actionLoading === "start" ? "Starting..." : "Start Agent"}
          </button>
          <button
            type="button"
            onClick={() => runAction("/agent/stop", "stop")}
            disabled={actionLoading !== ""}
            className="rounded-full bg-rose-600 px-5 py-3 text-sm font-semibold text-white transition hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {actionLoading === "stop" ? "Stopping..." : "Stop Agent"}
          </button>
          <button
            type="button"
            onClick={() => runAction("/agent/run-now", "run")}
            disabled={actionLoading !== ""}
            className="rounded-full bg-slate-900 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {actionLoading === "run" ? "Running..." : "Run Now"}
          </button>
        </div>

        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <div className="rounded-3xl border border-slate-200 bg-slate-50 p-5">
            <p className="text-sm font-semibold text-slate-900">Last run</p>
            <p className="mt-2 text-sm text-slate-600">{formatTimestamp(status.last_run_at)}</p>
            <p className="mt-4 text-sm font-semibold text-slate-900">Last success</p>
            <p className="mt-2 text-sm text-slate-600">{formatTimestamp(status.last_success_at)}</p>
          </div>
          <div className="rounded-3xl border border-slate-200 bg-slate-50 p-5">
            <p className="text-sm font-semibold text-slate-900">Last error</p>
            <p className="mt-2 text-sm text-slate-600">{status.last_error || "No recent errors"}</p>
          </div>
        </div>

        {error ? <div className="mt-6 rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div> : null}
      </div>

      <div className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-xl font-semibold text-slate-900">Agent Logs</h3>
            <p className="mt-1 text-sm text-slate-600">Recent polling, processing, and error events from `backend/logs/agent.log`.</p>
          </div>
          <button
            type="button"
            onClick={loadAgentData}
            className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
          >
            Refresh
          </button>
        </div>

        <div className="mt-5 overflow-hidden rounded-3xl border border-slate-200">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-4 py-3 font-semibold">Timestamp</th>
                <th className="px-4 py-3 font-semibold">Level</th>
                <th className="px-4 py-3 font-semibold">Message</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {logs.length === 0 ? (
                <tr>
                  <td colSpan="3" className="px-4 py-8 text-center text-slate-500">
                    No log entries yet.
                  </td>
                </tr>
              ) : (
                logs.map((entry, index) => (
                  <tr key={`${entry.timestamp}-${index}`}>
                    <td className="px-4 py-3 align-top text-slate-600">{entry.timestamp}</td>
                    <td className="px-4 py-3 align-top">
                      <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-700">{entry.level}</span>
                    </td>
                    <td className="px-4 py-3 align-top text-slate-800">{entry.message}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
