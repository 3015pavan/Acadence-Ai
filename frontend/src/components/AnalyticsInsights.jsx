import { useMemo } from "react";

export default function AnalyticsInsights({ students, summary, selectedFiles }) {
  const insights = useMemo(() => {
    if (!students || students.length === 0) {
      return {
        passRate: 0,
        topPerformers: [],
        atRiskStudents: [],
        inconsistentPerformers: [],
        recommendations: [],
      };
    }

    // Calculate pass rate
    const passed = students.filter((s) => s.pass_fail === "PASS").length;
    const passRate = Math.round((passed / students.length) * 100);

    // Find top performers (SGPA >= 8)
    const topPerformers = students
      .filter((s) => Number(s.sgpa) >= 8)
      .sort((a, b) => Number(b.sgpa) - Number(a.sgpa))
      .slice(0, 5);

    // Find at-risk students (SGPA < 5)
    const atRiskStudents = students
      .filter((s) => Number(s.sgpa) < 5)
      .sort((a, b) => Number(a.sgpa) - Number(b.sgpa))
      .slice(0, 5);

    // Find inconsistent performers (high variance between semesters)
    const inconsistentPerformers = students
      .filter((s) => {
        const semesters = Array.isArray(s.semesters) ? s.semesters : [];
        if (semesters.length < 2) return false;
        const sgpas = semesters.map((sem) => Number(sem.sgpa || 0));
        const mean = sgpas.reduce((a, b) => a + b, 0) / sgpas.length;
        const variance = sgpas.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / sgpas.length;
        return Math.sqrt(variance) > 1.5; // std dev > 1.5
      })
      .sort((a, b) => {
        const semA = Array.isArray(a.semesters) ? a.semesters : [];
        const semB = Array.isArray(b.semesters) ? b.semesters : [];
        const sgpasA = semA.map((s) => Number(s.sgpa || 0));
        const sgpasB = semB.map((s) => Number(s.sgpa || 0));
        const varA = sgpasA.reduce((sum, val) => {
          const mean = sgpasA.reduce((a, b) => a + b, 0) / sgpasA.length;
          return sum + Math.pow(val - mean, 2);
        }, 0) / sgpasA.length;
        const varB = sgpasB.reduce((sum, val) => {
          const mean = sgpasB.reduce((a, b) => a + b, 0) / sgpasB.length;
          return sum + Math.pow(val - mean, 2);
        }, 0) / sgpasB.length;
        return varB - varA;
      })
      .slice(0, 5);

    // Generate recommendations
    const recommendations = [];

    if (passRate < 70) {
      recommendations.push({
        type: "warning",
        title: "Low Pass Rate",
        message: `${100 - passRate}% of students are failing. Consider intervention programs.`,
      });
    } else if (passRate > 95) {
      recommendations.push({
        type: "success",
        title: "Excellent Pass Rate",
        message: "The dataset shows strong academic performance across students.",
      });
    }

    if (atRiskStudents.length > students.length * 0.2) {
      recommendations.push({
        type: "warning",
        title: "High Number of At-Risk Students",
        message: `${atRiskStudents.length} students have SGPA below 5. Additional support is recommended.`,
      });
    }

    if (inconsistentPerformers.length > 0) {
      recommendations.push({
        type: "info",
        title: "Inconsistent Performers Detected",
        message: `${inconsistentPerformers.length} students show significant performance variability. Consider targeted counseling.`,
      });
    }

    const avgSGPA = Number(summary?.average_sgpa || 0);
    if (avgSGPA > 7) {
      recommendations.push({
        type: "success",
        title: "Strong Average Performance",
        message: `Average SGPA of ${avgSGPA.toFixed(2)} indicates overall academic strength.`,
      });
    } else if (avgSGPA < 5) {
      recommendations.push({
        type: "warning",
        title: "Low Average SGPA",
        message: `Average SGPA is ${avgSGPA.toFixed(2)}. Curriculum review may be needed.`,
      });
    }

    return {
      passRate,
      topPerformers,
      atRiskStudents,
      inconsistentPerformers,
      recommendations,
    };
  }, [students, summary]);

  const getSourceLabel = (selectedFiles) => {
    if (!selectedFiles || selectedFiles.length === 0) return "All datasets";
    if (selectedFiles.length === 1) return "Single dataset";
    return `${selectedFiles.length} datasets`;
  };

  return (
    <section className="space-y-6">
      {/* Key Insights */}
      <div className="rounded-[2rem] border border-slate-200 bg-gradient-to-br from-blue-50 to-indigo-50 p-6 shadow-sm">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.28em] text-indigo-700">Analytics</p>
            <h3 className="mt-2 text-2xl font-semibold text-slate-900">Key Insights</h3>
            <p className="mt-1 text-sm text-slate-600">Analysis for {getSourceLabel(selectedFiles)}</p>
          </div>
          <div className="text-4xl font-bold text-indigo-600">{insights.passRate}%</div>
        </div>
        <p className="mt-3 text-lg font-medium text-slate-700">Pass Rate</p>
      </div>

      {/* Three-Column Metric Cards */}
      <div className="grid gap-5 md:grid-cols-3">
        {/* Top Performers */}
        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-700">Performance</p>
              <h4 className="mt-1 text-lg font-semibold text-slate-900">Top Performers</h4>
            </div>
            <div className="rounded-full bg-emerald-200 p-3">
              <svg className="h-6 w-6 text-emerald-700" fill="currentColor" viewBox="0 0 20 20">
                <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
              </svg>
            </div>
          </div>
          <div className="mt-4 space-y-2">
            {insights.topPerformers.slice(0, 3).map((student, idx) => (
              <div key={idx} className="flex items-center justify-between rounded-lg bg-white p-2">
                <span className="text-sm font-medium text-slate-700">{student.name}</span>
                <span className="rounded-full bg-emerald-200 px-2 py-1 text-xs font-semibold text-emerald-800">
                  {Number(student.sgpa).toFixed(2)}
                </span>
              </div>
            ))}
            {insights.topPerformers.length === 0 && (
              <p className="text-xs text-slate-500">No high performers in this dataset</p>
            )}
          </div>
        </div>

        {/* At-Risk Students */}
        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-5">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-rose-700">Attention</p>
              <h4 className="mt-1 text-lg font-semibold text-slate-900">At-Risk Students</h4>
            </div>
            <div className="rounded-full bg-rose-200 p-3">
              <svg className="h-6 w-6 text-rose-700" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd"
                  d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z"
                  clipRule="evenodd"
                />
              </svg>
            </div>
          </div>
          <div className="mt-4 space-y-2">
            {insights.atRiskStudents.slice(0, 3).map((student, idx) => (
              <div key={idx} className="flex items-center justify-between rounded-lg bg-white p-2">
                <span className="text-sm font-medium text-slate-700">{student.name}</span>
                <span className="rounded-full bg-rose-200 px-2 py-1 text-xs font-semibold text-rose-800">
                  {Number(student.sgpa).toFixed(2)}
                </span>
              </div>
            ))}
            {insights.atRiskStudents.length === 0 && (
              <p className="text-xs text-slate-500">No at-risk students in this dataset</p>
            )}
          </div>
        </div>

        {/* Inconsistent Performers */}
        <div className="rounded-2xl border border-amber-200 bg-amber-50 p-5">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-amber-700">Variability</p>
              <h4 className="mt-1 text-lg font-semibold text-slate-900">Variable Performance</h4>
            </div>
            <div className="rounded-full bg-amber-200 p-3">
              <svg className="h-6 w-6 text-amber-700" fill="currentColor" viewBox="0 0 20 20">
                <path d="M2 11a1 1 0 011-1h2a1 1 0 011 1v5a1 1 0 01-1 1H3a1 1 0 01-1-1v-5zM8 7a1 1 0 011-1h2a1 1 0 011 1v9a1 1 0 01-1 1H9a1 1 0 01-1-1V7zM14 4a1 1 0 011-1h2a1 1 0 011 1v12a1 1 0 01-1 1h-2a1 1 0 01-1-1V4z" />
              </svg>
            </div>
          </div>
          <div className="mt-4 space-y-2">
            {insights.inconsistentPerformers.slice(0, 3).map((student, idx) => (
              <div key={idx} className="flex items-center justify-between rounded-lg bg-white p-2">
                <span className="text-sm font-medium text-slate-700">{student.name}</span>
                <span className="rounded-full bg-amber-200 px-2 py-1 text-xs font-semibold text-amber-800">
                  {Number(student.sgpa).toFixed(2)}
                </span>
              </div>
            ))}
            {insights.inconsistentPerformers.length === 0 && (
              <p className="text-xs text-slate-500">No variable performers detected</p>
            )}
          </div>
        </div>
      </div>

      {/* Recommendations */}
      {insights.recommendations.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-lg font-semibold text-slate-900">Recommendations & Insights</h3>
          <div className="grid gap-3 md:grid-cols-2">
            {insights.recommendations.map((rec, idx) => {
              const colors = {
                success: "border-emerald-200 bg-emerald-50 text-emerald-900",
                warning: "border-rose-200 bg-rose-50 text-rose-900",
                info: "border-blue-200 bg-blue-50 text-blue-900",
              };
              return (
                <div key={idx} className={`rounded-xl border p-4 ${colors[rec.type]}`}>
                  <h4 className="font-semibold">{rec.title}</h4>
                  <p className="mt-1 text-sm opacity-90">{rec.message}</p>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
