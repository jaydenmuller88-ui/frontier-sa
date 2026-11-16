# Frontier SA (Still in progress)

Automated South African corporate, legal, and law enforcement intelligence platform powered by FastAPI, PostgreSQL, and local LLM entity extraction via Ollama.

## Architecture & Stack
- **API & Worker:** Python, FastAPI, httpx, feedparser
- **Database:** PostgreSQL (Docker)
- **Local AI / NLP:** Ollama (llama3.2) for entity extraction and event classification

## Monitored Data Sources
- **Moneyweb** (Financial & Corporate)
- **Daily Maverick** (Investigative & Policy)
- **BusinessTech** (Enterprise & Technology)
- **TechCentral** (ICT & Telecommunications)
- **SAnews** (Official Government & Law Enforcement Portal)

## Quick Start
1. Ensure Ollama is running locally with llama3.2 pulled (ollama run llama3.2).
2. Start the stack: docker-compose up --build -d
3. Access the FastAPI docs at http://localhost:8000/docs.
