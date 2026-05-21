import { Link } from "react-router-dom";

const highlights = [
  {
    title: "Upload to insight",
    description: "Drop in Excel or PDF result sheets and move through parsing, validation, and analytics in one flow.",
  },
  {
    title: "Search with context",
    description: "Use hybrid retrieval across the local FAISS index and Elasticsearch when it is available.",
  },
  {
    title: "Automated replies",
    description: "Email-driven processing can attach reports and respond in-thread to the original sender.",
  },
];

const stats = [
  { label: "Data sources", value: "Excel + PDF" },
  { label: "Retrieval", value: "FAISS + ES" },
  { label: "Delivery", value: "Reports + Replies" },
  { label: "Automation", value: "Gmail Agent" },
];

const workflow = [
  {
    step: "01",
    title: "Import files",
    description: "Upload academic result files or let Gmail ingestion pull attachments from connected inboxes.",
  },
  {
    step: "02",
    title: "Normalize records",
    description: "Parse student results, compute SGPA, and store structured rows in Postgres for reliable analysis.",
  },
  {
    step: "03",
    title: "Answer questions",
    description: "Query the processed data through hybrid search, intent detection, and contextual fallbacks.",
  },
  {
    step: "04",
    title: "Share reports",
    description: "Generate Excel and PDF outputs, then reply in-thread with the processed analysis package.",
  },
];

const demoSlides = [
  {
    title: "Ingest and preview",
    tag: "Upload",
    copy: "Drop in a file and see the first pass of parsing, validation, and record normalization.",
    accent: "from-emerald-400/30 via-white to-cyan-300/30",
  },
  {
    title: "Ask natural questions",
    tag: "Query",
    copy: "Use intent-aware search to find students, pass counts, subject results, and contextual answers.",
    accent: "from-brand-500/25 via-white to-sky-300/25",
  },
  {
    title: "Send the report back",
    tag: "Reply",
    copy: "Reply in-thread with the analysis report, processed workbook, and a clean summary for the sender.",
    accent: "from-amber-300/25 via-white to-brand-500/20",
  },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,rgba(38,141,130,0.12),transparent_26%),radial-gradient(circle_at_top_right,rgba(14,165,233,0.10),transparent_22%),linear-gradient(180deg,#eef6f4_0%,#f8fafc_42%,#eef6f4_100%)] text-slate-900">
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute left-[-8rem] top-24 h-72 w-72 rounded-full bg-brand-300/20 blur-3xl" />
        <div className="absolute right-[-6rem] top-[28rem] h-96 w-96 rounded-full bg-sky-300/15 blur-3xl" />
        <div className="absolute bottom-[-10rem] left-1/3 h-80 w-80 rounded-full bg-emerald-300/15 blur-3xl" />
      </div>

      <div className="relative mx-auto max-w-7xl px-4 py-4 sm:px-6 lg:px-8">
        <header className="sticky top-4 z-30 rounded-3xl border border-white/60 bg-white/75 px-5 py-4 shadow-soft backdrop-blur">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div className="flex items-center gap-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-700 to-brand-500 text-lg font-bold text-white shadow-soft">
                AAI
              </div>
              <div>
                <p className="text-sm uppercase tracking-[0.3em] text-brand-700">Acadence AI</p>
                <h1 className="text-lg font-semibold text-slate-950">Student Result Intelligence</h1>
              </div>
            </div>
            <nav className="flex flex-wrap gap-3">
              <a
                href="#capabilities"
                className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-brand-200 hover:text-brand-800"
              >
                Capabilities
              </a>
              <a
                href="#workflow"
                className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-brand-200 hover:text-brand-800"
              >
                Workflow
              </a>
              <a
                href="#start"
                className="rounded-full bg-brand-700 px-4 py-2 text-sm font-medium text-white shadow-soft transition hover:bg-brand-600"
              >
                Open App
              </a>
            </nav>
          </div>
        </header>

        <main className="space-y-24 py-8 sm:py-12 lg:py-16">
          <section className="grid items-center gap-12 lg:grid-cols-[1.08fr_0.92fr]">
            <div className="space-y-8">
              <div className="inline-flex rounded-full border border-brand-200 bg-white/80 px-4 py-2 text-sm font-medium text-brand-800 shadow-sm shadow-brand-100/50">
                Upload → Parse → Query → Report
              </div>
              <div className="space-y-5">
                <h2 className="max-w-3xl text-4xl font-semibold tracking-tight text-slate-950 sm:text-5xl lg:text-6xl">
                  A modern home for academic data, automation, and search.
                </h2>
                <p className="max-w-2xl text-base leading-7 text-slate-600 sm:text-lg">
                  Process student result files, sync searchable intelligence, and surface answers from the same pipeline used by the dashboard and Gmail automation.
                </p>
              </div>

              <div className="flex flex-wrap gap-4" id="start">
                <Link
                  to="/upload"
                  className="inline-flex items-center justify-center rounded-full bg-brand-700 px-6 py-3 text-sm font-semibold text-white shadow-soft transition hover:bg-brand-600"
                >
                  Start with Upload
                </Link>
                <Link
                  to="/dashboard"
                  className="inline-flex items-center justify-center rounded-full border border-slate-200 bg-white px-6 py-3 text-sm font-semibold text-slate-700 transition hover:border-brand-200 hover:text-brand-800"
                >
                  View Dashboard
                </Link>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              {stats.map((stat) => (
                <div key={stat.label} className="rounded-3xl border border-white/70 bg-white/80 p-6 shadow-sm backdrop-blur transition duration-300 hover:-translate-y-1 hover:shadow-lg">
                  <div className="text-sm text-slate-500">{stat.label}</div>
                  <div className="mt-2 text-2xl font-semibold text-slate-950">{stat.value}</div>
                </div>
              ))}
              <div className="sm:col-span-2 rounded-[2rem] border border-white/70 bg-gradient-to-br from-brand-900 via-brand-700 to-brand-500 p-6 text-white shadow-soft">
                <p className="text-sm uppercase tracking-[0.3em] text-brand-100">Now live</p>
                <h3 className="mt-3 text-2xl font-semibold">Built for inbox-driven ingestion</h3>
                <p className="mt-3 text-sm leading-6 text-brand-50">
                  Gmail attachments can be parsed, normalized, stored in Postgres, and replied to with generated analysis reports.
                </p>
              </div>
            </div>
          </section>

          <section id="capabilities" className="space-y-8 scroll-mt-28">
            <div className="max-w-2xl space-y-3">
              <p className="text-sm uppercase tracking-[0.3em] text-brand-700">Capabilities</p>
              <h3 className="text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl">
                Built for the full result-analysis workflow.
              </h3>
              <p className="text-base leading-7 text-slate-600">
                The interface focuses on a clear workflow: ingest files, normalize the data, explore the analytics, and send back a usable report.
              </p>
            </div>

            <div className="grid gap-4 md:grid-cols-3">
              {highlights.map((item) => (
                <div key={item.title} className="rounded-3xl border border-white/70 bg-white/85 p-6 shadow-sm backdrop-blur transition duration-300 hover:-translate-y-1 hover:shadow-lg">
                  <div className="mb-4 inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-brand-100 text-sm font-semibold text-brand-800">
                    {item.title.slice(0, 2)}
                  </div>
                  <h4 className="text-lg font-semibold text-slate-950">{item.title}</h4>
                  <p className="mt-3 text-sm leading-6 text-slate-600">{item.description}</p>
                </div>
              ))}
            </div>
          </section>

          <section id="workflow" className="space-y-8 scroll-mt-28">
            <div className="max-w-2xl space-y-3">
              <p className="text-sm uppercase tracking-[0.3em] text-brand-700">Workflow</p>
              <h3 className="text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl">
                A simple path from file upload to answer.
              </h3>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              {workflow.map((item) => (
                <div key={item.step} className="rounded-[2rem] border border-white/70 bg-white/85 p-6 shadow-sm backdrop-blur transition duration-300 hover:-translate-y-1 hover:shadow-lg">
                  <div className="text-sm font-semibold uppercase tracking-[0.3em] text-brand-700">{item.step}</div>
                  <h4 className="mt-3 text-xl font-semibold text-slate-950">{item.title}</h4>
                  <p className="mt-3 text-sm leading-6 text-slate-600">{item.description}</p>
                </div>
              ))}
            </div>
          </section>

          <section className="space-y-8">
            <div className="max-w-2xl space-y-3">
              <p className="text-sm uppercase tracking-[0.3em] text-brand-700">Demo slides</p>
              <h3 className="text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl">
                Scroll through the product story.
              </h3>
              <p className="text-base leading-7 text-slate-600">
                This strip keeps the page feeling dynamic and gives the homepage a presentation-like flow without leaving the page.
              </p>
            </div>

            <div className="flex snap-x snap-mandatory gap-5 overflow-x-auto pb-2 pr-1 [scrollbar-width:thin]">
              {demoSlides.map((slide, index) => (
                <article
                  key={slide.title}
                  className={`min-w-[280px] snap-start rounded-[2rem] border border-white/70 bg-gradient-to-br ${slide.accent} p-6 shadow-soft backdrop-blur sm:min-w-[340px] lg:min-w-[360px]`}
                >
                  <div className="flex items-center justify-between gap-4">
                    <span className="rounded-full bg-white/80 px-3 py-1 text-xs font-semibold uppercase tracking-[0.25em] text-brand-800">
                      {slide.tag}
                    </span>
                    <span className="text-sm font-medium text-slate-500">0{index + 1}</span>
                  </div>
                  <h4 className="mt-6 text-2xl font-semibold tracking-tight text-slate-950">{slide.title}</h4>
                  <p className="mt-4 text-sm leading-7 text-slate-700">{slide.copy}</p>
                  <div className="mt-8 overflow-hidden rounded-3xl border border-white/80 bg-white/75 p-4 shadow-sm">
                    <div className="h-2 rounded-full bg-slate-200">
                      <div className="h-2 rounded-full bg-gradient-to-r from-brand-600 to-sky-500" style={{ width: `${(index + 1) * 28}%` }} />
                    </div>
                    <div className="mt-4 grid grid-cols-3 gap-3">
                      <div className="rounded-2xl bg-slate-50 p-3">
                        <div className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Status</div>
                        <div className="mt-1 text-sm font-semibold text-slate-900">Ready</div>
                      </div>
                      <div className="rounded-2xl bg-slate-50 p-3">
                        <div className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Speed</div>
                        <div className="mt-1 text-sm font-semibold text-slate-900">Fast</div>
                      </div>
                      <div className="rounded-2xl bg-slate-50 p-3">
                        <div className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Output</div>
                        <div className="mt-1 text-sm font-semibold text-slate-900">Clear</div>
                      </div>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="grid gap-6 lg:grid-cols-[0.9fr_1.1fr]">
            <div className="rounded-[2rem] border border-white/70 bg-white/85 p-8 shadow-sm backdrop-blur transition duration-300 hover:-translate-y-1 hover:shadow-lg">
              <p className="text-sm uppercase tracking-[0.3em] text-brand-700">Designed for clarity</p>
              <h3 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">
                Clean visuals, stronger hierarchy, and enough space to breathe.
              </h3>
              <p className="mt-4 text-sm leading-7 text-slate-600">
                This layout keeps the focus on the product story while staying scrollable and easy to scan on desktop or mobile.
              </p>
            </div>

            <div className="rounded-[2rem] border border-white/70 bg-white/85 p-8 shadow-sm backdrop-blur transition duration-300 hover:-translate-y-1 hover:shadow-lg">
              <div className="grid gap-4 sm:grid-cols-3">
                {stats.map((stat) => (
                  <div key={`${stat.label}-footer`} className="rounded-2xl bg-slate-50 p-4">
                    <div className="text-xs uppercase tracking-[0.25em] text-slate-500">{stat.label}</div>
                    <div className="mt-2 text-base font-semibold text-slate-950">{stat.value}</div>
                  </div>
                ))}
              </div>
              <div className="mt-6 rounded-3xl bg-slate-950 px-6 py-5 text-white">
                <p className="text-sm uppercase tracking-[0.3em] text-brand-100">Get started</p>
                <p className="mt-2 text-sm leading-6 text-slate-300">
                  Open the app to upload a file, review the dashboard, or manage the Gmail agent.
                </p>
                <div className="mt-4 flex flex-wrap gap-3">
                  <Link
                    to="/upload"
                    className="rounded-full bg-white px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-brand-100"
                  >
                    Upload
                  </Link>
                  <Link
                    to="/agent"
                    className="rounded-full border border-white/20 px-4 py-2 text-sm font-semibold text-white transition hover:border-brand-200 hover:text-brand-100"
                  >
                    Agent Admin
                  </Link>
                </div>
              </div>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
