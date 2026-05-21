# Acadence AI

Acadence AI is a production-grade AI-powered academic automation platform designed for large-scale tabular educational datasets. The system combines adaptive Hybrid RAG, intelligent agents, SQL-grounded reasoning, semantic retrieval, and automation workflows to deliver accurate, grounded, and conversational insights over academic records in real time.
---

## Quickstart

Prerequisites:

- Python 3.9+ (3.11 recommended)
- Node.js 18+ and npm/yarn
- PostgreSQL running and reachable via `DATABASE_URL`

From the repository root:

```bash
# create and activate a venv
python -m venv .venv
.
# install backend deps
python -m pip install -r requirements.txt

# run backend (development)
cd backend
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

# frontend
cd ../frontend
npm install
npm run dev
```

API endpoints:

- Health: `GET /health` (http://127.0.0.1:8000/health)
- Docs: `http://127.0.0.1:8000/docs`
- Upload: `POST /upload` (multipart/form-data)

Data storage and indexes:

- Processed workbook: `backend/storage/processed_results.xlsx`
- FAISS index: `backend/storage/faiss/` (vector index files)
- Database: configured via `DATABASE_URL` (Postgres)

Environment variables (add to `.env`):

```
DATABASE_URL=postgresql://user:password@localhost:5432/acadence_ai_db
GCP_GEMINI_KEY=your_api_key_here
GEMINI_MODEL=gemini-2.5-flash
GEMINI_THINKING_LEVEL=low
GMAIL_SERVICE_ACCOUNT_JSON=path/to/credentials.json
ELASTICSEARCH_URL=http://localhost:9200
```

---
# Acadence AI

> AI-Powered Academic Intelligence Platform with Adaptive Hybrid + Agentic RAG

[![Status](https://img.shields.io/badge/status-production-brightgreen)](/)
[![Version](https://img.shields.io/badge/version-1.0.0-blue)](/)
[![License](https://img.shields.io/badge/license-MIT-green)](/)
[![Python](https://img.shields.io/badge/python-3.9+-3670A0?style=flat&logo=python)](/)
[![React](https://img.shields.io/badge/react-18.0+-61DAFB?style=flat&logo=react)](/)

Acadence AI is a production-grade AI-powered academic automation platform designed for large-scale tabular educational datasets. The system combines adaptive Hybrid RAG, intelligent agents, SQL-grounded reasoning, semantic retrieval, and automation workflows to deliver accurate, grounded, and conversational insights over academic records in real time.

Built for real-world educational institutions, the platform enables teachers, students, parents, and administrators to query complex academic datasets naturally while minimizing hallucinations through database-verified responses and grounded retrieval pipelines.

---

## Core Features

### Adaptive Hybrid + Agentic RAG

## Intelligent Query Orchestration

* Adaptive LLM-driven query planning
* Hybrid SQL + semantic retrieval orchestration
* Multi-step reasoning workflows
* Context reranking and validation
* Conversational multi-turn memory
* Hallucination prevention pipeline
* Database-grounded response generation
* Rule-based routing and intent handling as a fallback reliability layer
* Safe fallback execution for low-confidence or ambiguous queries


---

## Intelligent Academic Querying

Supports natural language questions such as:

- "Who are the top 10 students?"
- "Compare semester 4 toppers with semester 5 performance"
- "Which students are underperforming in DSA?"
- "Summarize class performance trends"
- "Which students improved the most?"

The system dynamically determines whether the query requires:

- SQL retrieval
- semantic retrieval
- hybrid retrieval
- analytics reasoning
- multi-step orchestration


---

## Key Capabilities

### Large-Scale Tabular Data Processing

- Handles massive academic datasets
- Multi-column student result processing
- CSV, Excel, and PDF ingestion
- Schema normalization and validation
- Duplicate detection and cleaning

### SQL-Grounded AI

- LLM dynamically generates SQL queries
- PostgreSQL acts as the source of truth
- Rankings, analytics, filtering, and aggregations are database verified
- Prevents fabricated academic values

### Semantic Retrieval

- FAISS-powered vector retrieval
- Context-aware semantic search
- Similarity-based chunk retrieval
- Reranking for high-confidence context selection

### Intelligent Agents

- Query planning agents
- Retrieval orchestration agents
- Analytics reasoning workflows
- Email automation agents
- Multi-step reasoning pipelines

### Email Automation

- Automated academic workflow processing
- Gmail attachment ingestion
- Automated report generation
- Notification workflows

---

## Architecture

```
User Query
    ↓
LLM Query Planner
    ↓
Dynamic Tool Selection

IF structured:
→ SQL generation
→ PostgreSQL execution
→ verified structured data

IF semantic:
→ FAISS retrieval
→ reranker
→ grounded context

IF hybrid:
→ SQL + semantic retrieval
→ context fusion
→ reranking

→ grounded LLM response
```

---

## Tech Stack

| Layer              | Technology                |
| ------------------ | ------------------------- |
| Frontend           | React, Vite, Tailwind CSS |
| Backend            | FastAPI, Python           |
| Database           | PostgreSQL                |
| Semantic Retrieval | FAISS                     |
| AI Layer           | Gemini / LLM              |
| Parsing            | LlamaParse, Pandas        |
| Automation         | Gmail Automation          |
| Cloud              | GCP, Cloud Storage        |
| Vector Embeddings  | Sentence Transformers     |
| Orchestration      | Agentic RAG Workflows     |

---

## Quick Start

### Prerequisites

- **Python** 3.9+
- **PostgreSQL** 12+
- **Node.js** 16+
- (Optional) Elasticsearch and Redis for advanced features

### 1️⃣ Clone the Repository

```bash
git clone https://github.com/3015pavan/Acadence_Ai.git 
cd agent_edata
```

### 2️⃣ Setup Backend

```bash
# Create Python virtual environment
python -m venv .venv

# Activate environment
.venv\Scripts\activate     # Windows
source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

### 3️⃣ Configure Environment

```bash
cp .env.example .env
# Edit .env with your:
# - PostgreSQL database URL
# - Gemini API key (GCP_GEMINI_KEY)
# - (Optional) Gmail service account credentials
```

### 4️⃣ Initialize Database

```bash
python -c "from backend.database import engine; from backend import models; models.Base.metadata.create_all(engine)"
```

### 5️⃣ Start Services

**Terminal 1 - Backend (FastAPI)**

```bash
cd backend
python -m uvicorn main:app --reload
# Backend runs on http://127.0.0.1:8000
```

**Terminal 2 - Frontend (React/Vite)**

```bash
cd frontend
npm install
npm run dev
# Frontend runs on http://127.0.0.1:5173
```

**Terminal 3 - Email Agent (Optional)**

```bash
# In a new terminal with activated venv:
python backend/agents/email_agent.py
# Automatically monitors Gmail for attachments
```

✅ **Done!** Your Acadence AI instance is now running. Navigate to `http://127.0.0.1:5173`

---

## Getting Started: Two Paths

### Path 1: Web Interface (Manual Upload)

Perfect for testing, ad-hoc analysis, and interactive exploration.

1. Navigate to `http://127.0.0.1:5173`
2. Go to **Upload Page** and drop an Excel or PDF file
3. View **Dashboard** with instant analytics
4. Use **Query Chat** for natural language questions

### Path 2: Email Automation (Hands-Free Processing)

Perfect for institutions with regular result batches.

1. **Setup Gmail Integration** (see [Email Automation Setup](#email-automation-setup))
2. Start the email agent: `python backend/agents/email_agent.py`
3. Send result files to the monitored Gmail address
4. Agent automatically:
   - ✅ Detects emails with attachments
   - ✅ Processes `.xlsx` and `.pdf` files
   - ✅ Generates PDF analysis reports
   - ✅ Replies with insights and download links

---

## API Reference

### Query Endpoint

```bash
POST /analytics/query
Content-Type: application/json

{
  "query": "Who are the top 5 performers?",
  "file_ids": [],           # Empty = all datasets
  "history": []             # Chat history (optional)
}
```

**Response:**

```json
{
  "intent": "CONTEXTUAL_ANSWER",
  "answer": "The top performers are...",
  "students": [
    {"name": "Student A", "sgpa": 9.2},
    {"name": "Student B", "sgpa": 9.0}
  ],
  "meta": {
    "confidence": 0.95,
    "citations": ["Student A: SGPA 9.2", "Student B: SGPA 9.0"]
  }
}
```

### Core Endpoints

```bash
# List all datasets
GET /analytics/datasets

# Upload file
POST /upload/file
Content-Type: multipart/form-data

# Delete dataset (cleanup)
DELETE /analytics/datasets/{dataset_id}

# Rebuild search index
POST /analytics/reindex
```

---

## Query Examples

| Your Question | Intent Type | Result |
|---|---|---|
| `Who are the toppers?` | SQL_QUERY | Database-verified list with SGPA |
| `Students with A+ but failed` | HYBRID | SQL + semantic analysis |
| `Average SGPA in DSA subject` | SQL_AGGREGATION | Database computed statistic |
| `Result of Abir in DSA` | SQL_LOOKUP | Database-verified record |
| `Pass rate by semester` | SQL_ANALYTICS | Cross-tabulated summary |
| `Summarize this class` | SEMANTIC + ANALYTICS | LLM narrative with DB-grounded insights |
| `Who needs support?` | HYBRID | At-risk identification with reasoning |

---

## Configuration

### Environment Variables (`.env`)

```bash
# Database Connection
DATABASE_URL=postgresql://user:password@localhost:5432/acadence_ai_db

# LLM Configuration (Google Gemini)
GCP_GEMINI_KEY=your_api_key_here
GEMINI_MODEL=gemini-2.5-flash
GEMINI_THINKING_LEVEL=low

# Gmail Integration (Optional)
GMAIL_SERVICE_ACCOUNT_JSON=path/to/credentials.json
GMAIL_INBOX_CHECK_INTERVAL=300  # seconds

# Search Backends (Optional)
ELASTICSEARCH_URL=http://localhost:9200
REDIS_URL=redis://localhost:6379

# API Configuration
API_PORT=8000
FRONTEND_URL=http://127.0.0.1:5173
```

---

## Email Automation Setup

**Automate your entire student results processing pipeline** — From inbox to insights in minutes.

### Step 1: Create Google Cloud Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use existing)
3. Create a new service account
4. Download private key as JSON (`credentials.json`)
5. Place in project root

### Step 2: Enable Gmail API

1. In Google Cloud Console, search for "Gmail API"
2. Click Enable
3. Go to Service Account details
4. Grant access to your Gmail address

### Step 3: Configure Environment

```bash
GMAIL_SERVICE_ACCOUNT_JSON=./credentials.json
GMAIL_INBOX_CHECK_INTERVAL=300
```

### Step 4: Start Email Agent

```bash
python backend/agents/email_agent.py
```

### Email Processing Workflow

```
📧 Incoming Email
   ↓
📎 Detect Attachment (.xlsx, .pdf)
   ↓
⚙️ Parse & Validate Data
   ↓
📊 Generate PDF Report
   ↓
💾 Store Results & Index
   ↓
✉️ Auto-Reply with Link
```

---

## Performance & Scaling

- **FAISS Indexing**: ~1M vectors, <100ms search latency
- **Elasticsearch**: Full-text search over 100K+ documents
- **PostgreSQL**: Sub-50ms indexed queries on 10K+ records
- **LLM Response Time**: ~2-5s (including context retrieval)
- **Cache Hit Rate**: 70%+ for repeated queries with Redis
- **Concurrent Users**: Tested with 50+ concurrent connections

---

## Reliability & Evaluation

The platform includes production-grade evaluation and monitoring pipelines for:

- Hallucination Rate
- Query Accuracy
- Recall@K
- Precision@K
- Groundedness Score
- Faithfulness Score
- SQL Execution Accuracy
- Retrieval Precision
- Multi-turn Context Accuracy
- Workflow Success Rate
- End-to-End Latency

---

## Testing

### Smoke Tests (Quick Validation)

```bash
# Start backend first, then:
python tools/smoke_test_query.py "Summarize this class"
```

### Query Validation Suite

```bash
python TEST_QUERIES_VALIDATION.py
```

### Integration Tests

```bash
pip install -r requirements.txt
pytest -v
```

### Evaluation Report

```bash
python tools/evaluate.py
```

---

## Use Cases

### Teachers

- Analyze student performance
- Identify weak students
- Compare semester trends
- Generate reports automatically

### Students

- Query marks and analytics
- Understand performance trends
- Track academic progress

### Parents

- Monitor student performance
- View attendance and results
- Understand strengths and weaknesses

### Institutions

- Automate result processing
- Generate analytics dashboards
- Enable AI-driven academic intelligence

---

## Documentation

### Learn More

- 📚 **Project Structure**: See [`backend/`](backend/) and [`frontend/`](frontend/) directories
- 🔌 **API Endpoints**: Full docs in [`backend/routes/`](backend/routes/)
- ⚙️ **Query Engine**: Core logic in [`backend/services/query_engine.py`](backend/services/query_engine.py)
- 🤖 **LLM Integration**: See [`backend/services/intelligence.py`](backend/services/intelligence.py)
- 📧 **Email Agent**: Details in [`backend/agents/email_agent.py`](backend/agents/email_agent.py)

---

## Roadmap

- [ ] Multimodal RAG support
- [ ] Predictive analytics
- [ ] Real-time streaming ingestion
- [ ] Advanced analytics agents
- [ ] Personalized academic recommendations
- [ ] Distributed vector search
- [ ] Role-aware reasoning pipelines
- [ ] Multi-language support

---

## Contributing

We welcome contributions! Here's how to get started:

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Commit** your changes (`git commit -m 'Add amazing feature'`)
4. **Push** to the branch (`git push origin feature/amazing-feature`)
5. **Open** a Pull Request with description

### Development Setup

```bash
git clone https://github.com/3015pavan/Acadence_Ai.git 
cd agent_edata
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

---

## Support & Troubleshooting

### Common Issues

**"LLM Provider not responding"**

```bash
# Check your API key
echo $GCP_GEMINI_KEY  # Should show your Gemini API key

# Test the connection
python -c "from backend.services.intelligence import _llm_chat_json"
```

**"Database connection failed"**

```bash
# Verify PostgreSQL is running
psql -U user -d acadence_ai_db -c "SELECT 1"

# Check DATABASE_URL in .env is correct
```

**"No results found for query"**

```bash
# Ensure files are uploaded
curl http://127.0.0.1:8000/analytics/datasets

# Check dashboard for parse errors
```

### Get Help

- 📖 Check [Documentation](#documentation)
- 🐛 [Open an Issue](https://github.com/3015pavan/Acadence_Ai/issues)
- 💬 [Start a Discussion](https://github.com/3015pavan/Acadence_Ai/discussions)

---

## Why Acadence AI?

Unlike traditional rule-based academic chatbots, Acadence AI uses adaptive Hybrid + Agentic RAG to dynamically reason over structured and semantic academic data without depending on fixed query templates or brittle intent pipelines.

The system is designed to answer arbitrary natural language questions over large academic datasets while maintaining grounded, verifiable, and reliable responses.

---

## Performance Goals

- Low hallucination rate
- High retrieval precision
- SQL-grounded correctness
- Adaptive query handling
- Production-grade scalability
- Real-time conversational analytics

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

## Author

**Pavan Reddy**

Building production-grade AI systems focused on adaptive RAG architectures, intelligent agents, semantic retrieval, automation workflows, and grounded AI reasoning over large-scale datasets.

---

**Acadence AI** — AI-Powered Academic Intelligence Platform

**Have questions?** [Open an Issue](https://github.com/3015pavan/Acadence_Ai/issues) | [Start a Discussion](https://github.com/3015pavan/Acadence_Ai/discussions) | [Email Us](mailto:support@acadence.ai)

⭐ **If Acadence AI helps you, please star us on GitHub!** [⭐ Star](https://github.com/3015pavan/Acadence_Ai)
