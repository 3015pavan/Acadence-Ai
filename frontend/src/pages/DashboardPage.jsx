import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import api from "../api";
import DashboardCharts from "../components/DashboardCharts";
import DatasetSelector from "../components/DatasetSelector";
import FileDropdown from "../components/FileDropdown";
import QueryChat from "../components/QueryChat";
import StudentTable from "../components/StudentTable";
import SummaryCard from "../components/SummaryCard";
import AnalyticsInsights from "../components/AnalyticsInsights";
import SubjectAnalysis from "../components/SubjectAnalysis";

function formatSgpa(value) {
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue.toFixed(2) : "0.00";
}

function normalizeStudents(students) {
  return Array.isArray(students) ? students : [];
}

function filterStudents(students, gradeFilter, sgpaRange) {
  const [minSgpa, maxSgpa] = sgpaRange;
  return students.filter((student) => {
    const results = Array.isArray(student.results) ? student.results : [];
    const matchesGrade =
      gradeFilter === "ALL" ||
      results.some((result) => (result.grade || "").toUpperCase() === gradeFilter);
    const numericSgpa = Number(student.sgpa);
    const matchesSgpa = Number.isFinite(numericSgpa) && numericSgpa >= minSgpa && numericSgpa <= maxSgpa;
    return matchesGrade && matchesSgpa;
  });
}

export default function DashboardPage() {
  const [summary, setSummary] = useState(null);
  const [students, setStudents] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reportLoading, setReportLoading] = useState(false);
  const [gradeFilter, setGradeFilter] = useState("ALL");
  const [sgpaRange, setSgpaRange] = useState([0, 10]);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const location = useLocation();
  const flashMessage = location.state?.flashMessage || "";

  useEffect(() => {
    const loadDashboard = async () => {
      setLoading(true);
      setError("");
      try {
        const params = selectedFiles.length ? { dataset_ids: selectedFiles.join(",") } : {};
        const [summaryResponse, studentsResponse] = await Promise.all([
          api.get("/analytics/summary", { params }),
          api.get("/analytics/students", { params }),
        ]);
        setSummary(summaryResponse.data);
        setStudents(normalizeStudents(studentsResponse.data.students));
      } catch (dashboardError) {
        setError(dashboardError.response?.data?.detail || "Unable to load dashboard data.");
      } finally {
        setLoading(false);
      }
    };

    loadDashboard();
  }, [selectedFiles]);

  const handleDownload = () => {
    window.open(`${api.defaults.baseURL}/analytics/download/processed`, "_blank");
  };

  const handleGenerateReport = async () => {
    setReportLoading(true);
    setError("");
    try {
      const response = await api.post("/analytics/report", {}, { responseType: "blob" });
      const blobUrl = window.URL.createObjectURL(new Blob([response.data], { type: "application/pdf" }));
      window.open(blobUrl, "_blank", "noopener,noreferrer");
      window.setTimeout(() => window.URL.revokeObjectURL(blobUrl), 60_000);
    } catch (reportError) {
      setError(reportError.response?.data?.detail || "Unable to generate the report.");
    } finally {
      setReportLoading(false);
    }
  };

  if (loading) {
    return <div className="rounded-3xl bg-white p-10 text-center text-slate-600 shadow-soft">Loading dashboard...</div>;
  }

  if (error) {
    return <div className="rounded-3xl bg-rose-50 p-10 text-center text-rose-700 shadow-soft">{error}</div>;
  }

  const safeSummary = summary || { topper: null, average_sgpa: 0, total_students: 0, failed_count: 0 };
  const allGrades = Array.from(
    new Set(
      students.flatMap((student) =>
        (Array.isArray(student.results) ? student.results : []).map((result) => (result.grade || "NA").toUpperCase()),
      ),
    ),
  ).sort((left, right) => left.localeCompare(right));
  const filteredStudents = filterStudents(students, gradeFilter, sgpaRange);

  return (
    <section className="space-y-6">
      {flashMessage ? (
        <div className="rounded-3xl border border-emerald-200 bg-emerald-50 px-5 py-4 text-sm font-medium text-emerald-800 shadow-sm">
          {flashMessage}
        </div>
      ) : null}

      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">Dashboard</h2>
          <p className="mt-1 text-sm text-slate-600">Explore the latest uploaded and processed academic result dataset.</p>
        </div>
        <div className="w-full md:w-64">
          <FileDropdown value={selectedFiles} onChange={setSelectedFiles} label="Filter by files" />
        </div>
      </div>

      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          onClick={handleGenerateReport}
          disabled={reportLoading}
          className="rounded-full bg-teal-600 px-5 py-3 text-sm font-semibold text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {reportLoading ? "Generating Report..." : "Generate Report"}
        </button>
        <button
          type="button"
          onClick={handleDownload}
          className="rounded-full bg-slate-900 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800"
        >
          Download Processed Excel
        </button>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          title="Topper"
          value={safeSummary.topper ? `${safeSummary.topper.name}` : "N/A"}
          subtitle={safeSummary.topper ? `${safeSummary.topper.usn} | SGPA ${formatSgpa(safeSummary.topper.sgpa)}` : "No data"}
        />
        <SummaryCard title="Average SGPA" value={formatSgpa(safeSummary.average_sgpa)} subtitle="Computed from PostgreSQL records" />
        <SummaryCard title="Total Students" value={safeSummary.total_students} subtitle="Current uploaded dataset" />
        <SummaryCard title="Failed Count" value={safeSummary.failed_count} subtitle="Students with at least one F grade" />
      </div>

      <div className="rounded-[1.75rem] border border-slate-200 bg-white p-4 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-teal-700">Active file scope</p>
        <p className="mt-2 text-sm text-slate-700">
          {selectedFiles.length
            ? `${selectedFiles.length} selected file(s) are driving the dashboard, query chat, grade distribution, and subject analysis.`
            : "No file filter selected; the dashboard is using all uploaded files."}
        </p>
      </div>

      <div className="grid gap-5 xl:grid-cols-[1.2fr_0.8fr]">
        <div className="rounded-[1.75rem] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex flex-col gap-4 md:flex-row md:items-end">
            <div className="flex-1">
              <label className="text-sm font-medium text-slate-700">Grade Filter</label>
              <select
                value={gradeFilter}
                onChange={(event) => setGradeFilter(event.target.value)}
                className="mt-2 w-full rounded-2xl border border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none focus:border-teal-500"
              >
                <option value="ALL">All grades</option>
                {allGrades.map((grade) => (
                  <option key={grade} value={grade}>
                    {grade}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex-1">
              <label className="text-sm font-medium text-slate-700">Minimum SGPA</label>
              <input
                type="range"
                min="0"
                max="10"
                step="0.1"
                value={sgpaRange[0]}
                onChange={(event) => setSgpaRange([Number(event.target.value), Math.max(Number(event.target.value), sgpaRange[1])])}
                className="mt-3 w-full accent-teal-600"
              />
              <p className="mt-1 text-sm text-slate-500">{sgpaRange[0].toFixed(1)}</p>
            </div>
            <div className="flex-1">
              <label className="text-sm font-medium text-slate-700">Maximum SGPA</label>
              <input
                type="range"
                min="0"
                max="10"
                step="0.1"
                value={sgpaRange[1]}
                onChange={(event) => setSgpaRange([Math.min(sgpaRange[0], Number(event.target.value)), Number(event.target.value)])}
                className="mt-3 w-full accent-slate-900"
              />
              <p className="mt-1 text-sm text-slate-500">{sgpaRange[1].toFixed(1)}</p>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2 text-sm text-slate-600">
            <span className="rounded-full bg-slate-100 px-3 py-1">Active grade: {gradeFilter}</span>
            <span className="rounded-full bg-slate-100 px-3 py-1">SGPA: {sgpaRange[0].toFixed(1)} to {sgpaRange[1].toFixed(1)}</span>
            <span className="rounded-full bg-teal-50 px-3 py-1 text-teal-800">Visible students: {filteredStudents.length}</span>
          </div>
        </div>

        <div className="rounded-[1.75rem] border border-slate-200 bg-white p-5 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-teal-700">Suggestions</p>
          <h3 className="mt-2 text-xl font-semibold text-slate-900">High-signal queries</h3>
          <div className="mt-4 flex flex-wrap gap-2">
            {["Try: topper", "Try: students with F", "Try: inconsistent performers", "Try: GP = 0 but also A grades"].map((item) => (
              <span key={item} className="rounded-full border border-teal-200 bg-teal-50 px-3 py-2 text-sm font-medium text-teal-800">
                {item}
              </span>
            ))}
          </div>
        </div>
      </div>

      <AnalyticsInsights students={filteredStudents} summary={safeSummary} selectedFiles={selectedFiles} />
      <DashboardCharts students={filteredStudents} />
      <SubjectAnalysis selectedFiles={selectedFiles} />
      <QueryChat selectedFiles={selectedFiles} onSelectedFilesChange={setSelectedFiles} />
      <StudentTable students={filteredStudents} title="Filtered Student Records" />
    </section>
  );
}
