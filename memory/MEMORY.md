# Sealine-Database Memory

## Active Schema Reference
Using **connections.md** as authoritative schema knowledge for all database interactions.

## Connection Details
- **Type**: SQL Server
- **Server**: ushou102-exap1
- **Database**: ai
- **Credentials**: See connections.md

## User Preferences
- Always show SQL queries used in responses for transparency
- Reports should be saved as both `.txt` and `.xlsx` (formatted with blue header row)

## Agent Tools (Web Chat UI + Terminal)
The `ClaudeChat` agent has 2 registered tools available in both the web chat and terminal:
1. **`execute_sql`** — run live SELECT queries against sealineDB
2. **`create_excel`** — generate a formatted .xlsx file from tabular data (blue header, frozen row, auto-width columns). Returns the temp file path.

**Typical agent workflow for "run report and email as Excel":**
1. Agent calls `execute_sql` to get the data
2. Agent calls `create_excel` with columns + rows → gets filepath


