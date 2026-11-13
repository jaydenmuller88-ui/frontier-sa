import os
from typing import Optional
import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

DATABASE_URL = os.getenv("DATABASE_URL")

app = FastAPI(title="Frontier SA Intelligence API", version="0.1.0")


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


@app.get("/api/stats")
def get_stats():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM documents;")
        docs = cur.fetchone()["count"]

        cur.execute("SELECT COUNT(*) AS count FROM entities WHERE entity_type = 'COMPANY';")
        companies = cur.fetchone()["count"]

        cur.execute("SELECT COUNT(*) AS count FROM entities WHERE entity_type = 'PERSON';")
        people = cur.fetchone()["count"]

        cur.execute("SELECT COUNT(*) AS count FROM mentions;")
        mentions = cur.fetchone()["count"]

        cur.execute(
            """
            SELECT e.id, e.display_name, e.entity_type, COUNT(m.id) AS mention_count
            FROM entities e
            JOIN mentions m ON e.id = m.entity_id
            GROUP BY e.id, e.display_name, e.entity_type
            ORDER BY mention_count DESC, e.display_name ASC
            LIMIT 10;
            """
        )
        top_entities = cur.fetchall()

    return {
        "documents": docs,
        "companies": companies,
        "people": people,
        "mentions": mentions,
        "top_entities": top_entities,
    }


@app.get("/api/mentions")
def list_mentions(
    q: Optional[str] = Query(default=None, description="Search entity name, headline, or quote"),
    event_type: Optional[str] = Query(default=None, description="Filter by event category"),
    entity_type: Optional[str] = Query(default=None, description="COMPANY or PERSON"),
    limit: int = Query(default=50, le=200),
):
    conditions = []
    params = []

    if q:
        conditions.append(
            "(e.display_name ILIKE %s OR d.title ILIKE %s OR m.exact_quote ILIKE %s)"
        )
        like_q = f"%{q.strip()}%"
        params.extend([like_q, like_q, like_q])

    if event_type and event_type != "ALL":
        conditions.append("m.event_type = %s")
        params.append(event_type.strip().upper())

    if entity_type and entity_type != "ALL":
        conditions.append("e.entity_type = %s")
        params.append(entity_type.strip().upper())

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    sql = f"""
        SELECT
            m.id AS mention_id,
            e.id AS entity_id,
            e.display_name AS entity_name,
            e.entity_type,
            m.event_type,
            m.exact_quote,
            d.id AS document_id,
            d.source_name,
            d.title AS article_title,
            d.source_url,
            d.created_at
        FROM mentions m
        JOIN entities e ON m.entity_id = e.id
        JOIN documents d ON m.document_id = d.id
        {where_clause}
        ORDER BY d.created_at DESC, m.id DESC
        LIMIT %s;
    """

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return {"count": len(rows), "results": rows}


@app.get("/api/entities/{entity_id}")
def get_entity_dossier(entity_id: int):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM entities WHERE id = %s;", (entity_id,))
        entity = cur.fetchone()
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        cur.execute(
            """
            SELECT
                m.event_type,
                m.exact_quote,
                d.source_name,
                d.title,
                d.source_url,
                d.created_at
            FROM mentions m
            JOIN documents d ON m.document_id = d.id
            WHERE m.entity_id = %s
            ORDER BY d.created_at DESC;
            """,
            (entity_id,),
        )
        mentions = cur.fetchall()

    return {"entity": entity, "mentions": mentions}


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Frontier SA — Corporate & Legal Intelligence</title>
  <style>
    :root {
      --bg: #0b0f17;
      --panel: #131a26;
      --border: #232f42;
      --text: #e6edf7;
      --muted: #8b9bb4;
      --accent: #38bdf8;
      --green: #34d399;
      --amber: #fbbf24;
      --red: #f87171;
      --purple: #a78bfa;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      padding: 20px 32px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #0e1420;
    }
    header h1 { margin: 0; font-size: 1.25rem; letter-spacing: 0.04em; }
    header span { color: var(--accent); }
    .container { max-width: 1280px; margin: 0 auto; padding: 24px 32px; }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 24px;
    }
    .stat-card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px 20px;
    }
    .stat-label { color: var(--muted); font-size: 0.8rem; text-transform: uppercase; }
    .stat-value { font-size: 1.75rem; font-weight: 700; margin-top: 6px; }
    .controls {
      display: flex;
      gap: 12px;
      margin-bottom: 20px;
      flex-wrap: wrap;
    }
    input, select, button {
      background: var(--panel);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 10px 14px;
      border-radius: 8px;
      font-size: 0.92rem;
    }
    input { flex: 1; min-width: 260px; }
    button {
      background: var(--accent);
      color: #06101d;
      font-weight: 600;
      cursor: pointer;
      border: none;
    }
    .layout {
      display: grid;
      grid-template-columns: 280px 1fr;
      gap: 24px;
    }
    .sidebar {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px;
      height: fit-content;
    }
    .sidebar h3 { margin: 0 0 12px; font-size: 0.9rem; color: var(--muted); text-transform: uppercase; }
    .entity-item {
      display: flex;
      justify-content: space-between;
      padding: 8px 10px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 0.9rem;
    }
    .entity-item:hover { background: #1c2638; }
    .feed { display: flex; flex-direction: column; gap: 14px; }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px 20px;
    }
    .card-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 8px;
    }
    .entity-pill {
      font-weight: 700;
      font-size: 1rem;
      color: var(--accent);
      cursor: pointer;
    }
    .badge {
      font-size: 0.72rem;
      font-weight: 700;
      padding: 3px 9px;
      border-radius: 999px;
      background: #1e293b;
      color: var(--text);
      border: 1px solid var(--border);
    }
    .badge.EXECUTIVE_CHANGE { color: var(--amber); border-color: rgba(251,191,36,0.4); }
    .badge.LITIGATION, .badge.LIQUIDATION { color: var(--red); border-color: rgba(248,113,113,0.4); }
    .badge.REGULATORY, .badge.TENDER { color: var(--purple); border-color: rgba(167,139,250,0.4); }
    .badge.FINANCIAL_RESULT { color: var(--green); border-color: rgba(52,211,153,0.4); }
    .badge.ENFORCEMENT_AND_CRIME { color: var(--red); border-color: rgba(248,113,113,0.4); }
    blockquote {
      margin: 10px 0;
      padding: 10px 14px;
      border-left: 3px solid var(--accent);
      background: #0e1420;
      color: #cbd5e1;
      font-size: 0.9rem;
      border-radius: 0 6px 6px 0;
    }
    .meta { font-size: 0.82rem; color: var(--muted); }
    .meta a { color: var(--text); text-decoration: none; }
    .meta a:hover { text-decoration: underline; }
    @media (max-width: 900px) {
      .stats { grid-template-columns: repeat(2, 1fr); }
      .layout { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>FRONTIER <span>SA</span> // INTELLIGENCE TERMINAL</h1>
    <div class="meta">Local-First Corporate & Legal Monitor &bull; <a href="/docs" target="_blank">OpenAPI Docs</a></div>
  </header>

  <div class="container">
    <div class="stats">
      <div class="stat-card"><div class="stat-label">Articles Ingested</div><div class="stat-value" id="stat-docs">-</div></div>
      <div class="stat-card"><div class="stat-label">Companies Tracked</div><div class="stat-value" id="stat-companies">-</div></div>
      <div class="stat-card"><div class="stat-label">Key People</div><div class="stat-value" id="stat-people">-</div></div>
      <div class="stat-card"><div class="stat-label">Total Mentions</div><div class="stat-value" id="stat-mentions">-</div></div>
    </div>

    <div class="controls">
      <input type="text" id="searchInput" placeholder="Search company, executive, or keyword (e.g. Spar, Sasol, Godongwana)..." />
      <select id="entityTypeSelect">
        <option value="ALL">All Entity Types</option>
        <option value="COMPANY">Companies Only</option>
        <option value="PERSON">People Only</option>
      </select>
      <select id="eventTypeSelect">
        <option value="ALL">All Event Types</option>
        <option value="EXECUTIVE_CHANGE">Executive Change</option>
        <option value="REGULATORY">Regulatory</option>
        <option value="LITIGATION">Litigation</option>
        <option value="LIQUIDATION">Liquidation</option>
        <option value="FINANCIAL_RESULT">Financial Result</option>
        <option value="TENDER">Tender</option>
        <option value="ENFORCEMENT_AND_CRIME">Enforcement & Crime</option>
        <option value="GENERAL">General</option>
      </select>
      <button onclick="loadMentions()">Search</button>
    </div>

    <div class="layout">
      <aside class="sidebar">
        <h3>Top Tracked Entities</h3>
        <div id="topEntities"></div>
      </aside>
      <main class="feed" id="feed"></main>
    </div>
  </div>

  <script>
    async function loadStats() {
      const res = await fetch('/api/stats');
      const data = await res.json();
      document.getElementById('stat-docs').textContent = data.documents;
      document.getElementById('stat-companies').textContent = data.companies;
      document.getElementById('stat-people').textContent = data.people;
      document.getElementById('stat-mentions').textContent = data.mentions;

      const list = document.getElementById('topEntities');
      list.innerHTML = data.top_entities.map(e => `
        <div class="entity-item" onclick="filterByEntity('${e.display_name.replace(/'/g, "\\'")}')">
          <span>${e.display_name}</span>
          <span class="badge">${e.mention_count}</span>
        </div>
      `).join('');
    }

    function filterByEntity(name) {
      document.getElementById('searchInput').value = name;
      loadMentions();
    }

    async function loadMentions() {
      const q = document.getElementById('searchInput').value;
      const entityType = document.getElementById('entityTypeSelect').value;
      const eventType = document.getElementById('eventTypeSelect').value;

      const params = new URLSearchParams({ q, entity_type: entityType, event_type: eventType });
      const res = await fetch('/api/mentions?' + params.toString());
      const data = await res.json();

      const feed = document.getElementById('feed');
      if (!data.results.length) {
        feed.innerHTML = '<div class="card">No matching intelligence records found.</div>';
        return;
      }

      feed.innerHTML = data.results.map(r => `
        <div class="card">
          <div class="card-top">
            <div>
              <span class="entity-pill" onclick="filterByEntity('${r.entity_name.replace(/'/g, "\\'")}')">${r.entity_name}</span>
              <span class="meta"> &bull; ${r.entity_type}</span>
            </div>
            <span class="badge ${r.event_type}">${r.event_type}</span>
          </div>
          <blockquote>"${r.exact_quote}"</blockquote>
          <div class="meta">
            <strong>${r.source_name}</strong>: <a href="${r.source_url}" target="_blank">${r.article_title}</a>
          </div>
        </div>
      `).join('');
    }

    document.getElementById('searchInput').addEventListener('keydown', e => {
      if (e.key === 'Enter') loadMentions();
    });

    loadStats();
    loadMentions();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML