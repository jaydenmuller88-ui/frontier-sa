import os
import time
import httpx
import psycopg
import feedparser
import trafilatura
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from typing import List, Literal

DATABASE_URL = os.getenv("DATABASE_URL")
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest")

FEEDS = [
    ("Moneyweb", "https://www.moneyweb.co.za/feed/"),
    ("Daily Maverick", "https://www.dailymaverick.co.za/dmrss/"),
    ("BusinessTech", "https://businesstech.co.za/news/feed/"),
    ("TechCentral", "https://techcentral.co.za/feed/"),
    ("SAPS Newsroom", "https://www.sanews.gov.za/taxonomy/term/47/feed"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}

class EntityExtraction(BaseModel):
    is_corporate_or_legal_news: bool = Field(
        description="True ONLY if the article is about business, companies, finance, economy, regulation, tenders, legal/court matters, or police/law enforcement operations (SAPS, Hawks arrests, raids, corruption crackdowns). False for sports, lifestyle, recipes, or general reader opinion."
    )
    companies: List[str] = Field(
        description="Exact names of companies, banks, tech firms, state-owned enterprises, law enforcement agencies (e.g., SAPS, Hawks, NPA), or regulators mentioned in the Title or Body."
    )
    people: List[str] = Field(
        description="Full names of executives, board members, judges, government officials, or suspects/officials mentioned."
    )
    event_type: Literal[
        "GENERAL",
        "LITIGATION",
        "LIQUIDATION",
        "TENDER",
        "EXECUTIVE_CHANGE",
        "FINANCIAL_RESULT",
        "REGULATORY",
        "ENFORCEMENT_AND_CRIME",
    ] = Field(
        description="Use GENERAL unless the article is specifically about: LITIGATION, LIQUIDATION, TENDER, EXECUTIVE_CHANGE, FINANCIAL_RESULT, REGULATORY, or ENFORCEMENT_AND_CRIME."
    )
    exact_quote: str = Field(
        description="A verbatim 1-2 sentence quote from the article supporting the extraction."
    )

def fetch_full_article(url: str, fallback_summary: str) -> str:
    try:
        resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=15.0)
        if resp.status_code == 200:
            extracted_text = trafilatura.extract(
                resp.text, include_comments=False, include_tables=False
            )
            if extracted_text and len(extracted_text) > 150:
                return extracted_text
    except Exception as e:
        print(f"   [WARN] Full-text scrape skipped ({e}), using RSS summary.")
    return fallback_summary



def extract_entities_with_ollama(title: str, article_text: str) -> EntityExtraction:
    schema = EntityExtraction.model_json_schema()
    base_url = OLLAMA_URL.rstrip("/").removesuffix("/api").removesuffix("/v1")
    model_name = OLLAMA_MODEL or "llama3.2:latest"

    prompt = f"""You are a South African corporate and legal intelligence analyst.
Carefully read BOTH the Title and Article Text below and extract structured intelligence.

Rules:
- Check the Title first! Any company, bank, tech firm, or agency in the Title (e.g. Microsoft, SARS, SAPS, Eskom) MUST be included in "companies".
- NEVER include countries, provinces, cities, or currencies (e.g., South Africa, Namibia, Zambia, ZiG) in "companies".
- Only classify event_type as LITIGATION, LIQUIDATION, TENDER, EXECUTIVE_CHANGE, FINANCIAL_RESULT, REGULATORY, or ENFORCEMENT_AND_CRIME if the article explicitly describes that event. Otherwise, strictly use GENERAL.
- Classify police raids, arrests, crime stats, and corruption investigations as ENFORCEMENT_AND_CRIME.
- Do NOT classify IT outages as TENDER. Do NOT classify sports or lifestyle articles as corporate events.

Title: {title}

Article Text:
{article_text[:3500]}
"""

    payload = {
        "model": model_name,
        "prompt": prompt,
        "format": schema,
        "options": {"temperature": 0},
        "stream": False,
    }

    try:
        response = httpx.post(f"{base_url}/api/generate", json=payload, timeout=90.0)
        if response.status_code != 200:
            print(f"   [ERROR] Ollama {response.status_code}: {response.text}")
            return EntityExtraction(
                is_corporate_or_legal_news=False,
                companies=[],
                people=[],
                event_type="GENERAL",
                exact_quote=title,
            )
        raw_content = response.json().get("response", "{}")
        parsed = EntityExtraction.model_validate_json(raw_content)
        parsed.companies = sorted({c.strip() for c in parsed.companies if len(c.strip()) > 1})
        parsed.people = sorted({p.strip() for p in parsed.people if len(p.strip()) > 1})
        return parsed
    except Exception as e:
        print(f"   [ERROR] Structured extraction failed: {e}")
        return EntityExtraction(
            is_corporate_or_legal_news=False,
            companies=[],
            people=[],
            event_type="GENERAL",
            exact_quote=title,
        )

def upsert_entity_and_mention(
    cur, doc_id: int, name: str, entity_type: str, event_type: str, exact_quote: str
):
    clean_name = name.strip()
    if len(clean_name) < 2:
        return
    canonical = clean_name.lower()

    cur.execute(
        """
        INSERT INTO entities (canonical_name, display_name, entity_type)
        VALUES (%s, %s, %s)
        ON CONFLICT (canonical_name) DO UPDATE SET display_name = EXCLUDED.display_name
        RETURNING id;
        """,
        (canonical, clean_name, entity_type),
    )
    entity_res = cur.fetchone()
    if entity_res:
        cur.execute(
            """
            INSERT INTO mentions (document_id, entity_id, event_type, exact_quote)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING;
            """,
            (doc_id, entity_res[0], event_type, exact_quote),
        )

def run_ingestion_pipeline():
    print("--- Starting Ingestion & Extraction Cycle ---")

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for source_name, feed_url in FEEDS:
                print(f"Fetching {source_name}: {feed_url}")
                try:
                    resp = httpx.get(
                        feed_url, headers=HEADERS, follow_redirects=True, timeout=15.0, verify=False
                    )
                    parsed_feed = feedparser.parse(resp.text)
                except Exception as feed_err:
                    print(f"[ERROR] Failed to fetch {source_name}: {feed_err}")
                    continue

                print(f"Found {len(parsed_feed.entries)} entries.")
                for entry in parsed_feed.entries[:5]:
                    title = entry.get("title", "No Title").strip()
                    link = entry.get("link", "").strip()
                    summary = entry.get("summary", title).strip()

                    if not link:
                        continue

                    cur.execute(
                        "SELECT id FROM documents WHERE source_url = %s;", (link,)
                    )
                    if cur.fetchone():
                        continue

                    print(f"-> Scraping & Extracting: {title}")
                    full_text = fetch_full_article(link, summary)

                    cur.execute(
                        """
                        INSERT INTO documents (source_name, source_url, title, raw_text, summary)
                        VALUES (%s, %s, %s, %s, %s) RETURNING id;
                        """,
                        (source_name, link, title, full_text, summary),
                    )
                    doc_id = cur.fetchone()[0]

                    extracted = extract_entities_with_ollama(title, full_text)
                    print(
                        f"   Signal: {extracted.is_corporate_or_legal_news} | Companies: {extracted.companies} | People: {extracted.people} | Event: {extracted.event_type}"
                    )

                    if not extracted.is_corporate_or_legal_news:
                        conn.commit()
                        continue

                    for company in extracted.companies:
                        upsert_entity_and_mention(
                            cur, doc_id, company, "COMPANY", extracted.event_type, extracted.exact_quote
                        )

                    for person in extracted.people:
                        upsert_entity_and_mention(
                            cur, doc_id, person, "PERSON", extracted.event_type, extracted.exact_quote
                        )

                    conn.commit()

            print("--- Ingestion Cycle Complete ---")

if __name__ == "__main__":
    time.sleep(5)
    print("--- Frontier SA Worker Running Pipeline ---")
    run_ingestion_pipeline()
    while True:
        time.sleep(3600)