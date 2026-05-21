import { useEffect, useState } from "react";
import api from "../api";

export default function FileDropdown({ value = [], onChange, label = "Select Files" }) {
  const [datasets, setDatasets] = useState([]);
  const [loading, setLoading] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    api
      .get("/analytics/datasets")
      .then((r) => {
        if (!mounted) return;
        setDatasets(Array.isArray(r.data) ? r.data : []);
      })
      .catch(() => setDatasets([]))
      .finally(() => setLoading(false));
    return () => {
      mounted = false;
    };
  }, []);

  const toggle = (id) => {
    const exists = value.includes(id);
    const next = exists ? value.filter((v) => v !== id) : [...value, id];
    onChange(next);
  };

  const deleteDataset = async (dataset) => {
    const sourceLabel = dataset.source || "upload";
    const confirmed = window.confirm(
      `Delete ${dataset.name}? This removes its ${sourceLabel} data, related records, and generated files.`
    );
    if (!confirmed) {
      return;
    }

    setDeletingId(dataset.id);
    setActionError("");
    try {
      await api.delete(`/analytics/datasets/${dataset.id}`);
      setDatasets((current) => current.filter((item) => item.id !== dataset.id));
      onChange(value.filter((selectedId) => selectedId !== dataset.id));
    } catch (error) {
      setActionError(error.response?.data?.detail || "Unable to delete the selected file.");
    } finally {
      setDeletingId(null);
    }
  };

  const sourceColor = (source) => {
    if (source === "email") return "text-blue-600 bg-blue-50";
    return "text-green-600 bg-green-50";
  };

  const sourceBadgeColor = (source) => {
    if (source === "email") return "bg-blue-100 text-blue-700";
    return "bg-green-100 text-green-700";
  };

  return (
    <div className="relative inline-block w-full">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full rounded-lg border border-slate-300 bg-white px-4 py-2 text-left text-sm font-medium text-slate-900 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-teal-500"
      >
        <div className="flex items-center justify-between">
          <span>
            {value.length === 0
              ? label
              : `${value.length} file${value.length !== 1 ? "s" : ""} selected`}
          </span>
          <svg
            className={`h-5 w-5 transition-transform ${isOpen ? "rotate-180" : ""}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M19 14l-7 7m0 0l-7-7m7 7V3"
            />
          </svg>
        </div>
      </button>

      {isOpen && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setIsOpen(false)}
          />
          <div className="absolute top-full left-0 right-0 z-50 mt-1 rounded-lg border border-slate-300 bg-white shadow-lg">
            <div className="max-h-64 overflow-auto">
              {loading ? (
                <div className="px-4 py-3 text-sm text-slate-500">Loading files…</div>
              ) : datasets.length ? (
                <>
                  <div className="sticky top-0 flex gap-2 border-b border-slate-200 bg-slate-50 px-4 py-2">
                    <button
                      type="button"
                      onClick={() => onChange(datasets.map((d) => d.id))}
                      className="text-xs font-medium text-teal-600 hover:underline"
                    >
                      Select All
                    </button>
                    <button
                      type="button"
                      onClick={() => onChange([])}
                      className="text-xs font-medium text-slate-500 hover:underline"
                    >
                      Clear
                    </button>
                  </div>
                  {datasets.map((dataset) => (
                    <div
                      key={dataset.id}
                      className={`flex items-center gap-3 px-4 py-3 hover:bg-slate-50 ${sourceColor(dataset.source || "upload")}`}
                    >
                      <label className="flex flex-1 cursor-pointer items-center gap-3 min-w-0">
                        <input
                          type="checkbox"
                          checked={value.includes(dataset.id)}
                          onChange={() => toggle(dataset.id)}
                          className="h-4 w-4"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium">{dataset.name}</div>
                          <div className="text-xs opacity-70">ID: {dataset.id}</div>
                        </div>
                      </label>
                      <button
                        type="button"
                        onClick={() => deleteDataset(dataset)}
                        disabled={deletingId === dataset.id}
                        className="rounded-full border border-rose-200 bg-rose-50 px-3 py-1 text-xs font-semibold text-rose-700 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {deletingId === dataset.id ? "Deleting..." : "Delete"}
                      </button>
                      <span
                        className={`whitespace-nowrap rounded-full px-2 py-1 text-xs font-medium ${sourceBadgeColor(
                          dataset.source || "upload"
                        )}`}
                      >
                        {dataset.source || "upload"}
                      </span>
                    </div>
                  ))}
                </>
              ) : (
                <div className="px-4 py-3 text-sm text-slate-500">No files available</div>
              )}
            </div>
          </div>
        </>
      )}

      {actionError ? <p className="mt-2 text-xs font-medium text-rose-600">{actionError}</p> : null}

      {value.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {datasets
            .filter((d) => value.includes(d.id))
            .map((dataset) => (
              <span
                key={dataset.id}
                className="inline-flex items-center gap-1 rounded-full bg-teal-100 px-3 py-1 text-xs font-medium text-teal-800"
              >
                {dataset.name}
                <button
                  type="button"
                  onClick={() => toggle(dataset.id)}
                  className="font-bold hover:opacity-70"
                >
                  ×
                </button>
              </span>
            ))}
        </div>
      )}
    </div>
  );
}
