function formatSgpa(value) {
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue.toFixed(2) : "0.00";
}

export default function StudentTable({ students = [], title = "Student Records" }) {
  const normalizedStudents = Array.isArray(students) ? students : [];

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-200 px-4 py-4">
        <h3 className="text-lg font-semibold text-slate-900">{title}</h3>
        <p className="mt-1 text-sm text-slate-500">{normalizedStudents.length} student record(s) in the current view.</p>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">USN</th>
              <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Name</th>
              <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">SGPA</th>
              <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Status</th>
              <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Subjects</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {!normalizedStudents.length ? (
              <tr>
                <td colSpan="5" className="px-4 py-8 text-center text-sm text-slate-500">
                  No students match the current filters.
                </td>
              </tr>
            ) : null}
            {normalizedStudents.map((student) => {
              const results = Array.isArray(student.results) ? student.results : [];

              return (
                <tr key={student.usn} className="align-top">
                  <td className="px-4 py-4 text-sm font-medium text-slate-800">{student.usn}</td>
                  <td className="px-4 py-4 text-sm text-slate-700">{student.name}</td>
                  <td className="px-4 py-4 text-sm text-slate-700">{formatSgpa(student.sgpa)}</td>
                  <td className="px-4 py-4 text-sm">
                    <span
                      className={`rounded-full px-3 py-1 font-medium ${
                        student.pass_fail === "FAIL"
                          ? "bg-rose-100 text-rose-700"
                          : "bg-emerald-100 text-emerald-700"
                      }`}
                    >
                      {student.pass_fail}
                    </span>
                  </td>
                  <td className="px-4 py-4 text-sm text-slate-700">
                    <div className="flex flex-wrap gap-2">
                      {results.map((result) => (
                        <span key={`${student.usn}-${result.subject}`} className="rounded-full bg-slate-100 px-3 py-1">
                          {result.subject}: {result.grade}
                          {result.gp !== null ? ` (${result.gp})` : ""}
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
