# Sealine Database Agent — Current Implementation

## Overview

We have a Claude agent that has access to the Searates database with the ability to run queries and write reports off of user terminal chats.

### Current Capabilities of the Agent

* **Live SQL Query Execution** — Runs read-only SQL queries (SELECT, WITH/CTE, EXEC) against the Sealine `searates` SQL Server database in real time, with results capped at 500 rows and a 30-second timeout per query
* **Natural Language Data Analysis** — Translates plain-English user questions into SQL queries using cached schema documentation, relationship mappings, and join patterns loaded from markdown reference files
* **Streaming Conversational Interface** — Provides a multi-turn terminal chat (REPL) with real-time streaming responses, multi-line input support, ANSI color formatting, and slash commands (`/clear`, `/history`, `/docs`, `/system`, `/quit`, `/help`)
* **Report Generation & Export** — Generates formatted reports in text (.txt) and Excel (.xlsx) formats with professional styling (blue headers, frozen panes), and supports email delivery via Gmail API

The agent also bypasses firewall or SSL permission issues to send calls to both run SQL on the database and send API calls to Claude — specifically, SSL certificate verification is disabled via `httpx.Client(verify=False)` on the Anthropic API client to work through corporate proxy/firewall environments that intercept HTTPS traffic.

The agent responds to user requests and autonomously decides when to query the database, constructs appropriate SQL using its cached schema knowledge, executes the query, interprets the results, and presents findings in a conversational format — looping through multiple tool calls if needed before delivering a final answer.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                  claude_desktop.py                           │
│                  (Terminal Chat Application)                  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  REPL (Read-Eval-Print Loop)                           │  │
│  │  • Multi-line input (double-Enter to submit)           │  │
│  │  • Slash command parsing (/clear, /history, etc.)      │  │
│  │  • ANSI color terminal output                          │  │
│  └────────────────────┬───────────────────────────────────┘  │
│                       │                                      │
│  ┌────────────────────▼───────────────────────────────────┐  │
│  │  ClaudeChat Class                                      │  │
│  │  • Anthropic SDK client (SSL verify disabled)          │  │
│  │  • Conversation message history                        │  │
│  │  • Token usage & cache hit tracking                    │  │
│  │  • SQL call counter                                    │  │
│  └────────────────────┬───────────────────────────────────┘  │
│                       │                                      │
│  ┌────────────────────▼───────────────────────────────────┐  │
│  │  System Prompt + Cached Context                        │  │
│  │  • Base system prompt (data analyst role)              │  │
│  │  • 5 markdown files loaded with ephemeral cache        │  │
│  │    └─ schema, relationships, connections, reports,     │  │
│  │       memory                                           │  │
│  │  • Dynamic SQL tool instructions                       │  │
│  └────────────────────┬───────────────────────────────────┘  │
│                       │                                      │
│  ┌────────────────────▼───────────────────────────────────┐  │
│  │  Agentic Tool-Use Loop                                 │  │
│  │  • Streaming via client.messages.stream()              │  │
│  │  • Detects stop_reason == "tool_use"                   │  │
│  │  • Calls _execute_tool() → appends tool_result        │  │
│  │  • Loops until stop_reason == "end_turn"               │  │
│  └────────────────────┬───────────────────────────────────┘  │
│                       │                                      │
│  ┌────────────────────▼───────────────────────────────────┐  │
│  │  execute_sql Tool                                      │  │
│  │  • Query validation (SELECT/WITH/EXEC only)            │  │
│  │  • PyODBC connection to SQL Server                     │  │
│  │  • Fetches up to 500 rows                              │  │
│  │  • Formats as pipe-delimited ASCII table               │  │
│  │  • Returns result text to Claude                       │  │
│  └────────────────────┬───────────────────────────────────┘  │
│                       │                                      │
│  ┌────────────────────▼───────────────────────────────────┐  │
│  │  SQL Server: searates                                  │  │
│  │  Server: ushou102-exap1                                │  │
│  │  Driver: ODBC Driver 17 for SQL Server                 │  │
│  │  Core tables: Sealine_Header, Sealine_Container,       │  │
│  │  Sealine_Container_Event, Sealine_Locations,           │  │
│  │  Sealine_Vessels, Sealine_Route, Sealine_Facilities    │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## Project File Structure

```
Sealine-Database/
├── claude_desktop.py                   # Main terminal chat agent (489 lines)
├── query_intransit.py                  # Standalone report: in-transit containers (74 lines)
├── generate_map.py                     # War zone container map generator (451 lines)
├── memory/                             # Cached context documents (auto-loaded)
│   ├── MEMORY.md                       #   Quick reference & user preferences
│   ├── sealineDB_schema.md             #   Full database schema (245 lines)
│   ├── relationships.md                #   Table join logic & data model (101 lines)
│   ├── connections.md                  #   Database connection details (24 lines)
│   └── reports.md                      #   Saved report catalog & SQL (75 lines)
├── Transit container in different city report.txt   # Generated report output
├── Transit container in different city report.xlsx  # Generated Excel report
└── current_implementation.md           # This document
```

---

## Core Components

### 1. Terminal Chat Interface (`claude_desktop.py`)

The primary entry point. A Python terminal application that provides an interactive Claude-powered chat with live database access.

**Invocation:**
```bash
python claude_desktop.py                          # Default: Haiku model, DB enabled
python claude_desktop.py --model claude-sonnet-4-6  # Use Sonnet model
python claude_desktop.py --no-db                  # Disable SQL tool
python claude_desktop.py --no-docs                # Skip markdown context loading
python claude_desktop.py --system "Custom prompt" # Override system prompt
```

**Key Class: `ClaudeChat`**
- Manages the Anthropic API client, conversation history, and tool execution
- Tracks cumulative token usage (input, output, cache reads) and SQL call count
- Supports extended thinking blocks with graceful fallback for models that don't support it

**Slash Commands:**

| Command | Action |
|---------|--------|
| `/clear` | Wipe conversation history |
| `/history` | Show turn count, token usage, cache hits, SQL calls |
| `/docs` | List loaded markdown context files |
| `/system` | View or change the system prompt |
| `/help` | Show available commands |
| `/quit` | Exit the application |

**Startup Banner:**
```
╔══════════════════════════════════════════╗
║  Claude for Desktop  (terminal edition)  ║
╚══════════════════════════════════════════╝
```

### 2. SQL Execution Engine (`run_sql()`)

A safety-gated function that executes read-only SQL against the Sealine database.

**Safety Controls:**
- **Allowlist validation**: Only `SELECT`, `WITH`, `EXEC`, and `EXECUTE` queries permitted (first-word check)
- **Row limit**: Results capped at 500 rows (`MAX_ROWS = 500`)
- **Connection timeout**: 30-second timeout on database connections
- **Error capture**: SQL exceptions returned as error text to Claude for self-correction

**Output Format:**
- Column headers with dynamic width alignment
- Pipe-delimited rows
- Truncation notice when results exceed 500 rows

**Tool Definition (passed to Claude API):**
```json
{
    "name": "execute_sql",
    "description": "Execute a read-only SQL query against the Sealine searates database (SQL Server). Use this to answer questions with live data. Only SELECT and WITH (CTE) statements are allowed. Results are capped at 500 rows.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The SQL query to execute (SELECT or WITH only)."
            }
        },
        "required": ["query"]
    }
}
```

### 3. Agentic Tool-Use Loop

The conversation loop implements a full agentic pattern:

1. **User sends message** → appended to conversation history
2. **Streaming API call** → sent with full message history + system prompt + tools
3. **Response streamed** → text displayed character-by-character in terminal
4. **Tool use detected** → if `stop_reason == "tool_use"`:
   - Extract tool name and input from response content blocks
   - Execute `run_sql()` with the generated query
   - Display the SQL query being executed (in yellow)
   - Append tool result to messages
   - **Loop back to step 2** (Claude can chain multiple queries)
5. **End turn** → if `stop_reason == "end_turn"`, return to user prompt

This allows Claude to autonomously run multiple queries in sequence to answer complex questions.

### 4. Cached Context System

At startup, all `.md` files from the `memory/` directory are loaded and concatenated into a single text block. This block is passed to the API with Anthropic's **ephemeral prompt caching**, reducing token costs on subsequent turns.

**Cached Documents:**

| File | Content | Purpose |
|------|---------|---------|
| `sealineDB_schema.md` | Complete table and column definitions for 14+ tables | Enables accurate SQL generation |
| `relationships.md` | Join patterns, FK relationships, location hierarchies | Guides complex multi-table queries |
| `connections.md` | Server, database, credentials, driver info | Reference (connection is hardcoded in code) |
| `reports.md` | Catalog of saved report queries | Reusable SQL patterns |
| `MEMORY.md` | Quick reference, user preferences, implementation notes | Behavioral guidance |

**System Prompt Construction:**
- Base prompt: "You are Claude, a helpful AI assistant and data analyst for the Sealine shipping database."
- Cached context: All markdown files concatenated with `cache_control: {"type": "ephemeral"}`
- Dynamic note: SQL tool usage instructions appended when `--no-db` is not set

---

## Database: Sealine Searates

### Connection Details

| Parameter | Value |
|-----------|-------|
| Type | Microsoft SQL Server |
| Server | `ushou102-exap1` |
| Database | `searates` |
| Driver | ODBC Driver 17 for SQL Server |
| Username | `sean` |
| Timeout | 30 seconds |

### Core Schema

**Sealine_Header** — Root shipment record
- `TrackNumber` (PK), `Sealine_Code`, `Status`, `POL`, `POD`, `Carrier`, `CreateDt`, `DeletedDt`

**Sealine_Container** — Containers per shipment (~4 per shipment)
- `TrackNumber`, `Container_NUMBER`, `Status`, `Container_Size_Type`

**Sealine_Container_Event** — Tracking events per container
- `TrackNumber`, `Container_NUMBER`, `Order_Id`, `Location`, `Facility`, `EventDescription`, `EventDate`, `Actual` (1=confirmed, 0=estimated), `DeletedDt`

**Sealine_Locations** — Route stops with coordinates
- `TrackNumber`, `Id`, `Name`, `Country`, `LOCode`, `Lat` (VARCHAR), `Lng` (VARCHAR)

**Sealine_Vessels** — Vessel/carrier information
- `TrackNumber`, `Name`, `imo`, `call_sign`, `flag`

**Sealine_Route** — Scheduled and actual route dates
- `TrackNumber`, `RouteType` (ETD/ETA/ATD/ATA), `Date`, `IsActual`

**Sealine_Facilities** — Port/terminal facility data
- `TrackNumber`, `Id`, `name`, `Locode`

### Data Quality Notes

- **No enforced FK constraints** — relationships are logical only; always join on `TrackNumber` AND specific ID columns
- **Soft deletes** — filter active records with `WHERE DeletedDt IS NULL`
- **Lat/Lng stored as VARCHAR** — must use `TRY_CAST(Lat AS FLOAT)` for geographic operations
- **Actual vs Estimated events** — `Actual = 1` for confirmed events, `Actual = 0` for estimated
- **Archive tables exist** — `Sealine_Container_Event_02May2025`, `Sealine_Container_Event_Revised`, etc.
- **Order_Id for chronology** — use `TRY_CAST(Order_Id AS INT)` for event sequencing

---

## Standalone Scripts

### `query_intransit.py` — In-Transit Container Report

Generates a report of IN_TRANSIT shipments where containers are currently located in different cities.

**Logic:**
1. Uses a CTE to find the latest actual event per container (by `Order_Id DESC`)
2. Joins to `Sealine_Locations` for city names
3. Groups by `TrackNumber` and filters where `COUNT(DISTINCT City) > 1`
4. Outputs: TrackNumber | Sealine_Code | ContainerCount | DistinctCities | CityList

**Filters:** `Status = 'IN_TRANSIT'`, `Sealine_Code <> 'DHC2'`, `Actual = 1`, `DeletedDt IS NULL`

**Output:** Console only (pipe-delimited text table)

### `generate_map.py` — War Zone Container Tracking Map

Generates an interactive HTML map showing container positions in geopolitical war zones.

**War Zones Tracked:**
- Red Sea
- Gulf of Aden
- Persian Gulf
- Eastern Mediterranean

**Features:**
- Google Maps JavaScript API integration
- Color-coded markers by war zone
- MarkerClusterer for grouped display
- Info windows with container details
- Legend with per-zone container counts
- Statistics panel (total containers, shipments, status breakdown)

**Input:** `warzone_shipments.csv` (hardcoded path)
**Output:** `warzone_map.html` (self-contained interactive HTML file)

---

## SSL / Firewall Bypass

The agent operates in a corporate network environment where HTTPS traffic is intercepted by a proxy/firewall. Two bypass mechanisms are in place:

### 1. Anthropic API Calls

```python
self.client = anthropic.Anthropic(
    http_client=httpx.Client(verify=False)
)
```

SSL certificate verification is disabled on the `httpx` HTTP client passed to the Anthropic SDK. This prevents certificate validation errors caused by the corporate proxy's SSL inspection (which replaces upstream certificates with its own CA).

### 2. SQL Server Connection

The PyODBC connection to SQL Server uses the standard ODBC Driver 17 connection string without explicit SSL/TLS configuration. The connection relies on the driver's default behavior and the network's internal routing to reach the database server (`ushou102-exap1`) without certificate issues.

---

## API Integration

### Anthropic Claude API

| Parameter | Value |
|-----------|-------|
| SDK | `anthropic` Python package |
| HTTP Client | `httpx.Client(verify=False)` |
| Default Model | `claude-haiku-4-5` |
| Alternate Models | `claude-sonnet-4-6`, others via `--model` flag |
| Streaming | Yes (`client.messages.stream()`) |
| Tool Calling | Yes (single tool: `execute_sql`) |
| Prompt Caching | Yes (ephemeral cache on system context) |
| API Key | Environment variable `ANTHROPIC_API_KEY` (validated at startup) |

### Error Handling

| Error Type | Handling |
|------------|----------|
| `AuthenticationError` | Print error, prompt to check API key |
| `RateLimitError` | Print error, ask user to wait |
| `APIConnectionError` | Print error, suggest checking network |
| `APIStatusError` | Print status code and message |
| `BadRequestError` | Retry without extended thinking (model compatibility fallback) |
| SQL exceptions | Return error text to Claude for self-correction |

---

## Report Output Formats

| Format | Description | Styling |
|--------|-------------|---------|
| **Console** | Pipe-delimited ASCII table with ANSI colors | Dynamic column widths, color-coded sections |
| **Text (.txt)** | Plain text report file | Pipe-delimited table format |
| **Excel (.xlsx)** | Formatted spreadsheet via `openpyxl` | Blue header row (#1F4788), white text, frozen pane row 1, auto-width columns |
| **Email** | Excel attachment via Gmail API | Sent to `hangseanshi@gmail.com` |
| **HTML Map** | Interactive Google Maps visualization | Color-coded markers, clustering, info windows, legend |

---

## User Preferences (from memory)

- SQL queries should be shown transparently during execution
- Excel reports: blue header (#1F4788), frozen top row, auto-sized columns
- Email delivery: via Gmail to `hangseanshi@gmail.com`
- Preferred analysis style: data-driven with live SQL results

---

## Dependencies

```
anthropic      # Anthropic Claude API SDK
httpx           # HTTP client (custom SSL config)
pyodbc          # SQL Server ODBC connectivity
openpyxl        # Excel file generation
```

**System Requirements:**
- Python 3.6+
- ODBC Driver 17 for SQL Server
- Network access to `ushou102-exap1` (SQL Server)
- Network access to Anthropic API (with or without proxy)
- Environment variable: `ANTHROPIC_API_KEY`

---

## Git Status

| Field | Value |
|-------|-------|
| Branch | `main` |
| Latest Commit | `7d3e2a9` — "initial version" |
| Status | Clean (no uncommitted changes) |
