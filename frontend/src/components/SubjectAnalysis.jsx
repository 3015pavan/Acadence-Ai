import React, { useState, useEffect } from "react";
import api from "../api";

export default function SubjectAnalysis({ selectedFiles = [] }) {
  const [subjectData, setSubjectData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expandedSubject, setExpandedSubject] = useState(null);

  useEffect(() => {
    const fetchSubjectAnalysis = async () => {
      try {
        const params = selectedFiles.length ? `?dataset_ids=${selectedFiles.join(",")}` : "";
        const response = await api.get(`/analytics/subject-wise${params}`);
        setSubjectData(response.data);
        setError(null);
      } catch (err) {
        setError(err.message);
        setSubjectData(null);
      } finally {
        setLoading(false);
      }
    };

    fetchSubjectAnalysis();
  }, [selectedFiles]);

  if (loading) return <div className="text-center py-8 text-gray-500">Loading subject analysis...</div>;
  if (error) return <div className="text-center py-8 text-red-500">Error: {error}</div>;
  if (!subjectData) return <div className="text-center py-8 text-gray-500">No subject data available</div>;

  const getGradeColor = (grade) => {
    const colors = {
      O: "bg-green-100 text-green-800",
      "A+": "bg-blue-100 text-blue-800",
      A: "bg-blue-50 text-blue-700",
      "B+": "bg-yellow-100 text-yellow-800",
      B: "bg-yellow-50 text-yellow-700",
      C: "bg-orange-100 text-orange-800",
      D: "bg-red-100 text-red-800",
      F: "bg-red-200 text-red-900",
    };
    return colors[grade] || "bg-gray-100 text-gray-800";
  };

  const getDifficultyBadge = (difficulty) => {
    const colors = {
      Easy: "bg-green-100 text-green-800",
      Medium: "bg-yellow-100 text-yellow-800",
      Hard: "bg-red-100 text-red-800",
    };
    return colors[difficulty] || "bg-gray-100 text-gray-800";
  };

  return (
    <div className="p-6 bg-white rounded-lg shadow-md">
      <div className="mb-6 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-800">Subject-Wise Analysis</h2>
          <p className="text-sm text-gray-500">{selectedFiles.length ? `${selectedFiles.length} selected file(s)` : "All files"}</p>
        </div>
      </div>

      {/* Weak and Strong Subjects Summary */}
      <div className="grid grid-cols-2 gap-4 mb-8">
        {/* Weak Subjects */}
        <div className="bg-red-50 p-4 rounded-lg border border-red-200">
          <h3 className="font-semibold text-red-900 mb-3">🔴 Weak Subjects</h3>
          <div className="space-y-2">
            {subjectData.weak_subjects && subjectData.weak_subjects.length > 0 ? (
              subjectData.weak_subjects.map((subject, idx) => (
                <div key={idx} className="text-sm">
                  <div className="font-medium text-gray-800">
                    {subject.subject.split(":")[0].trim()}
                  </div>
                  <div className="text-gray-600">Avg GP: {subject.avg_gp.toFixed(2)}</div>
                </div>
              ))
            ) : (
              <p className="text-sm text-gray-600">No weak subjects</p>
            )}
          </div>
        </div>

        {/* Strong Subjects */}
        <div className="bg-green-50 p-4 rounded-lg border border-green-200">
          <h3 className="font-semibold text-green-900 mb-3">🟢 Strong Subjects</h3>
          <div className="space-y-2">
            {subjectData.strong_subjects && subjectData.strong_subjects.length > 0 ? (
              subjectData.strong_subjects.map((subject, idx) => (
                <div key={idx} className="text-sm">
                  <div className="font-medium text-gray-800">
                    {subject.subject.split(":")[0].trim()}
                  </div>
                  <div className="text-gray-600">Avg GP: {subject.avg_gp.toFixed(2)}</div>
                </div>
              ))
            ) : (
              <p className="text-sm text-gray-600">No strong subjects</p>
            )}
          </div>
        </div>
      </div>

      {/* All Subjects Table */}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead className="bg-gray-100 border-b-2 border-gray-300">
            <tr>
              <th className="px-4 py-2 text-left font-semibold text-gray-700">Subject</th>
              <th className="px-4 py-2 text-center font-semibold text-gray-700">Students</th>
              <th className="px-4 py-2 text-center font-semibold text-gray-700">Avg GP</th>
              <th className="px-4 py-2 text-center font-semibold text-gray-700">Difficulty</th>
              <th className="px-4 py-2 text-center font-semibold text-gray-700">Grade Distribution</th>
            </tr>
          </thead>
          <tbody>
            {subjectData.subjects && subjectData.subjects.length > 0 ? (
              subjectData.subjects.map((subject, idx) => (
                <React.Fragment key={idx}>
                  <tr
                    className="border-b hover:bg-gray-50 cursor-pointer"
                    onClick={() => setExpandedSubject(expandedSubject === idx ? null : idx)}
                  >
                    <td className="px-4 py-3 text-sm font-medium text-gray-800">
                      {subject.subject.split(":")[0].trim()}
                    </td>
                    <td className="px-4 py-3 text-center text-sm text-gray-600">
                      {subject.total_students}
                    </td>
                    <td className="px-4 py-3 text-center text-sm font-semibold text-gray-800">
                      {subject.avg_gp.toFixed(2)}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <span className={`text-xs px-2 py-1 rounded-full font-medium ${getDifficultyBadge(subject.difficulty)}`}>
                        {subject.difficulty}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-center">
                      <button className="text-blue-600 hover:text-blue-800 text-sm font-medium">
                        {expandedSubject === idx ? "Hide" : "Show"}
                      </button>
                    </td>
                  </tr>

                  {/* Expanded Grade Distribution */}
                  {expandedSubject === idx && (
                    <tr className="bg-blue-50 border-b">
                      <td colSpan="5" className="px-4 py-4">
                        <div className="grid grid-cols-4 gap-3 md:grid-cols-8">
                          {Object.entries(subject.grade_distribution || {})
                            .sort(([gradeA], [gradeB]) => {
                              const order = ["O", "A+", "A", "B+", "B", "C", "D", "F"];
                              return order.indexOf(gradeA) - order.indexOf(gradeB);
                            })
                            .map(([grade, count]) => (
                              <div key={grade} className="text-center">
                                <span className={`text-xs px-2 py-1 rounded font-semibold ${getGradeColor(grade)} block mb-1`}>
                                  {grade}
                                </span>
                                <span className="text-sm font-medium text-gray-700">{count}</span>
                              </div>
                            ))}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))
            ) : (
              <tr>
                <td colSpan="5" className="px-4 py-3 text-center text-gray-500">
                  No subject data available
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-6 text-sm text-gray-600">
        <p>Total Subjects: <span className="font-semibold text-gray-800">{subjectData.total_subjects || 0}</span></p>
      </div>
    </div>
  );
}
