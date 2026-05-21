import { useEffect, useState } from "react";
import api from "../api";

export default function DatasetSelector({ value = [], onChange, showSource = true, compact = false }) {
  const [datasets, setDatasets] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [filterSource, setFilterSource] = useState("all"); // "all", "upload", "email"

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

  const clear = () => onChange([]);
  const selectAll = () => onChange(datasets.map((d) => d.id));

  const filtered = datasets.filter((d) => {
    const matchesSearch = d.name.toLowerCase().includes(search.toLowerCase());
    const matchesSource = filterSource === "all" || (d.source || "upload") === filterSource;
    return matchesSearch && matchesSource;
  });

  const sourceColor = (source) => {
    if (source === "email") return "bg-blue-100 text-blue-700";
    return "bg-green-100 text-green-700";
  };

  if (compact) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-2">
        <div className="flex items-center justify-between gap-2 mb-2">
          <div className="text-xs font-semibold">Files</div>
          <div className="flex gap-1">
            <button type="button" onClick={selectAll} className="text-xs text-teal-600 hover:underline">
              All
            </button>
            <button type="button" onClick={clear} className="text-xs text-slate-500 hover:underline">
              Clear
            </button>
          </div>
        </div>
        <div className="max-h-32 overflow-auto space-y-1">
          {loading ? (
            <div className="text-xs text-slate-500">Loading…</div>
          ) : filtered.length ? (
            filtered.map((d) => (
              <label key={d.id} className="flex cursor-pointer items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={value.includes(d.id)}
                  onChange={() => toggle(d.id)}
                  className="h-3 w-3"
                />
                <span className="flex-1 truncate">{d.name}</span>
                {showSource && (
                  <span className={`whitespace-nowrap rounded px-1 py-0.5 text-xs font-medium ${sourceColor(d.source || "upload")}`}>
                    {d.source || "upload"}
                  </span>
                )}
              </label>
            ))
          ) : (
            <div className="text-xs text-slate-500">No files</div>
          )}
        </div>
        <div className="mt-2 text-xs text-slate-500">Selected: {value.length}</div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="text-sm font-semibold">Select Files</div>
        <div className="flex gap-2">
          <button type="button" onClick={selectAll} className="text-xs text-teal-600 hover:underline">
            Select all
          </button>
          <button type="button" onClick={clear} className="text-xs text-slate-500 hover:underline">
            Clear
          </button>
        </div>
      </div>

      <div className="flex flex-col gap-3 md:flex-row md:gap-2">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search files…"
          className="flex-1 rounded-lg border border-slate-100 px-3 py-2 text-sm"
        />
        {showSource && (
          <select
            value={filterSource}
            onChange={(e) => setFilterSource(e.target.value)}
            className="rounded-lg border border-slate-100 bg-white px-3 py-2 text-sm"
          >
            <option value="all">All sources</option>
            <option value="upload">Uploads</option>
            <option value="email">Email</option>
          </select>
        )}
      </div>

      <div className="mt-3 max-h-48 overflow-auto space-y-2">
        {loading ? (
          <div className="text-sm text-slate-500">Loading files…</div>
        ) : filtered.length ? (
          filtered.map((d) => (
            <label key={d.id} className="flex cursor-pointer items-center gap-3 rounded-lg p-2 hover:bg-slate-50">
              <input
                type="checkbox"
                checked={value.includes(d.id)}
                onChange={() => toggle(d.id)}
                className="h-4 w-4"
              />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium truncate">{d.name}</div>
                <div className="text-xs text-slate-500">ID: {d.id}</div>
              </div>
              {showSource && (
                <span className={`whitespace-nowrap rounded-full px-2 py-1 text-xs font-medium ${sourceColor(d.source || "upload")}`}>
                  {d.source || "upload"}
                </span>
              )}
            </label>
          ))
        ) : (
          <div className="text-sm text-slate-500">No files found</div>
        )}
      </div>

      <div className="mt-3 text-xs text-slate-500">Selected: {value.length}</div>
    </div>
  );
}
