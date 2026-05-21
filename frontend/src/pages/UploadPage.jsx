import { useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../api";
import FileDropdown from "../components/FileDropdown";

export default function UploadPage() {
  const [file, setFile] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const navigate = useNavigate();

  const handleUpload = async (event) => {
    event.preventDefault();
    if (!file) {
      setError("Choose an Excel or PDF file before uploading.");
      return;
    }

    setLoading(true);
    setError("");
    setMessage("");

    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await api.post("/upload", formData, {
        headers: {
          "Content-Type": "multipart/form-data",
        },
      });
      const successMessage = `Processed ${response.data.total_students} students successfully.`;
      setMessage(successMessage);
      navigate("/dashboard", {
        state: {
          flashMessage: successMessage,
        },
      });
    } catch (uploadError) {
      setError(uploadError.response?.data?.detail || "Upload failed.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="space-y-6">
      <div className="rounded-3xl border border-slate-200 bg-white p-6">
        <h3 className="text-lg font-semibold text-slate-900 mb-3">View Existing Files</h3>
        <FileDropdown value={selectedFiles} onChange={setSelectedFiles} label="Select files to view details" />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
        <div className="rounded-3xl bg-white p-6 shadow-soft">
          <h2 className="text-2xl font-semibold text-slate-900">Upload Results File</h2>
          <p className="mt-2 text-sm text-slate-600">
            Supported formats are <span className="font-medium">.xlsx</span> and <span className="font-medium">.pdf</span>.
            The backend detects headers dynamically, cleans the dataset, computes missing SGPA values, and syncs PostgreSQL, Elasticsearch, and the semantic intent store.
          </p>

          <form onSubmit={handleUpload} className="mt-6 space-y-4">
            <label className="flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-brand-300 bg-brand-50 px-6 py-12 text-center">
              <span className="text-base font-medium text-brand-900">Drop a file here or click to browse</span>
              <span className="mt-2 text-sm text-brand-700">{file ? file.name : "No file selected yet"}</span>
              <input
                type="file"
                accept=".xlsx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                className="hidden"
                onChange={(event) => setFile(event.target.files?.[0] || null)}
              />
            </label>

            <button
              type="submit"
              disabled={loading}
              className="rounded-full bg-brand-700 px-5 py-3 text-sm font-semibold text-white transition hover:bg-brand-800 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loading ? "Processing..." : "Upload and Analyze"}
            </button>
          </form>

          {message ? <p className="mt-4 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</p> : null}
          {error ? <p className="mt-4 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</p> : null}
        </div>

        <div className="rounded-3xl border border-slate-200 bg-white p-6">
          <h3 className="text-lg font-semibold text-slate-900">What happens after upload</h3>
          <div className="mt-4 space-y-3 text-sm text-slate-600">
            <p>The parser reads every sheet or table, resolves multi-row headers, removes empty rows, and normalizes student records.</p>
            <p>Pandas computes missing SGPA values, marks failures when any grade equals F, and prepares the processed Excel output.</p>
            <p>FastAPI stores the clean dataset in PostgreSQL, mirrors searchable student documents into Elasticsearch, and refreshes the semantic intent map.</p>
          </div>
        </div>
      </div>
    </section>
  );
}
