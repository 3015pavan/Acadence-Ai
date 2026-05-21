function DistributionBars({ title, items, colorClass }) {
  const normalizedItems = Array.isArray(items) ? items : [];
  const maxValue = Math.max(...normalizedItems.map((item) => item.value), 1);

  return (
    <div className="rounded-[1.75rem] border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="text-lg font-semibold text-slate-900">{title}</h3>
      <div className="mt-5 space-y-3">
        {normalizedItems.map((item) => (
          <div key={item.label}>
            <div className="mb-1 flex items-center justify-between text-sm text-slate-600">
              <span>{item.label}</span>
              <span className="font-semibold text-slate-900">{item.value}</span>
            </div>
            <div className="h-3 rounded-full bg-slate-100">
              <div
                className={`h-3 rounded-full ${colorClass}`}
                style={{ width: `${(item.value / maxValue) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function DashboardCharts({ students }) {
  const gradeCounts = {};
  const passFailCounts = { PASS: 0, FAIL: 0 };
  const normalizedStudents = Array.isArray(students) ? students : [];

  normalizedStudents.forEach((student) => {
    const results = Array.isArray(student.results) ? student.results : [];
    const status = student.pass_fail || "PASS";
    passFailCounts[status] = (passFailCounts[status] || 0) + 1;
    results.forEach((result) => {
      const grade = (result.grade || "NA").toUpperCase();
      gradeCounts[grade] = (gradeCounts[grade] || 0) + 1;
    });
  });

  const gradeItems = Object.entries(gradeCounts)
    .sort((left, right) => left[0].localeCompare(right[0]))
    .map(([label, value]) => ({ label, value }));

  const ratioItems = Object.entries(passFailCounts).map(([label, value]) => ({ label, value }));

  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <DistributionBars title="Grade Distribution" items={gradeItems.length ? gradeItems : [{ label: "No data", value: 0 }]} colorClass="bg-teal-500" />
      <DistributionBars title="Pass / Fail Ratio" items={ratioItems} colorClass="bg-slate-900" />
    </div>
  );
}
