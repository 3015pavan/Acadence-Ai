import { useEffect, useState } from "react";
import api from "../api";

export default function FileFilter({ value = [], onChange }) {
  const [datasets, setDatasets] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
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
    const confirmed = window.confirm(
      `Delete ${dataset.name}? This removes its data and generated files from the backend.`
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

  const clear = () => onChange([]);
  const selectAll = () => onChange(datasets.map((d) => d.id));

  const filtered = datasets.filter((d) => d.name.toLowerCase().includes(search.toLowerCase()));

  const sourceColor = (source) => {
    if (source === "email") return "bg-blue-100 text-blue-700";
    return "bg-green-100 text-green-700";
  };

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm font-semibold">Files</div>
        <div className="flex gap-2">
          <button type="button" onClick={selectAll} className="text-xs text-teal-600 hover:underline">
            Select all
          </button>
          <button type="button" onClick={clear} className="text-xs text-slate-500 hover:underline">
            Clear
          </button>
        </div>
      </div>

      <div className="mt-2 flex items-center gap-2">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search files..."
          className="flex-1 rounded-md border border-slate-100 px-2 py-1 text-sm"
        />
      </div>

      <div className="mt-3 max-h-40 overflow-auto">
        {loading ? (
          <div className="text-sm text-slate-500">Loading files…</div>
        ) : filtered.length ? (
          filtered.map((d) => (
            <div key={d.id} className="mt-2 flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-slate-50">
              <label className="flex flex-1 cursor-pointer items-center gap-3">
                <input
                  type="checkbox"
                  checked={value.includes(d.id)}
                  onChange={() => toggle(d.id)}
                  className="h-4 w-4"
                />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium">{d.name}</div>
                  <div className="text-xs text-slate-500">ID: {d.id}</div>
                </div>
              </label>
              <button
                type="button"
                onClick={() => deleteDataset(d)}
                disabled={deletingId === d.id}
                className="rounded-full border border-rose-200 bg-rose-50 px-3 py-1 text-xs font-semibold text-rose-700 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {deletingId === d.id ? "Deleting..." : "Delete"}
              </button>
              <span className={`whitespace-nowrap rounded-full px-2 py-1 text-xs font-medium ${sourceColor(d.source || "upload")}`}>
                {d.source || "upload"}
              </span>
            </div>
          ))
        ) : (
          <div className="text-sm text-slate-500">No files available</div>
        )}
      </div>

      {actionError ? <div className="mt-2 text-xs font-medium text-rose-600">{actionError}</div> : null}

      <div className="mt-3 text-xs text-slate-500">Selected: {value.length}</div>
    </div>
  );
}
