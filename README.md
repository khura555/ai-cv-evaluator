LLM-powered automated scoring system for CVs and project reports

Tech Stack: Django · DRF · Celery · Redis · Qdrant · PostgreSQL · Gemini 2.5 Flash · text-embedding-004

AI CV & Project Evaluator is an end-to-end automated evaluation system designed to assess:

CV / Resume quality
* Technical project reports
* Candidate-job fit
* Overall hiring readiness

It extracts text from uploaded PDFs, embeds the content using Gemini text-embedding-004, enriches context through Qdrant vector search, and generates structured scoring using Gemini 2.5 Flash.

All long-running tasks (parsing, embeddings, vector search, LLM evaluation) run asynchronously via Celery + Redis.

This project is designed for engineers who want a real-world RAG pipeline integrated with LLM reasoning, vector search, and async job orchestration.

flowchart LR
    A[User Uploads PDF] --> B[Django API]
    B --> C[Redis Queue (Celery)]
    C --> D[Celery Worker]

    D -->|Extract Text| E[PDF Parser]
    D -->|Generate Embedding| G[Gemini text-embedding-004]
    G --> F[Qdrant Vector Database]

    D -->|Vector Search| F
    D -->|LLM Evaluation| H[Gemini 2.5 Flash]

    H --> I[(PostgreSQL)]
    I --> B

    B --> J[User Receives Evaluation Result]

Requirements
Python 3.10+
PostgreSQL 13+
Redis Server
Qdrant (Docker OR embedded mode)
Google API Key (Gemini)

