# Sealine-Database Memory

## Active Schema Reference
Using **sealineDB_schema.md** as authoritative schema knowledge for all database interactions.

## Connection Details
- **Type**: SQL Server
- **Server**: ushou102-exap1
- **Database**: ai
- **Credentials**: See connections.md

## User Preferences
- Always show SQL queries used in responses for transparency
- Reports should be saved as both `.txt` and `.xlsx` (formatted with blue header row)
- Reusable reports are stored as Python scripts and catalogued in `memory/reports.md`
- Email reports as Excel attachments to `hangseanshi@gmail.com` using Gmail API

## Email / Output Capabilities
- **Gmail API**: OAuth2 via `token.json` in `C:\Users\hangs\OneDrive\GitHub\OpenExxon\`
- **Gmail address**: loaded from `OpenExxon/.env` → `GMAIL_ADDRESS`
- **Excel**: `openpyxl` — blue header (#1F4788), freeze pane row 1
- **Always send to**: hangseanshi@gmail.com unless told otherwise

## Agent Tools (Web Chat UI + Terminal)
The `ClaudeChat` agent has 3 registered tools available in both the web chat and terminal:
1. **`execute_sql`** — run live SELECT queries against sealineDB
2. **`create_excel`** — generate a formatted .xlsx file from tabular data (blue header, frozen row, auto-width columns). Returns the temp file path.
3. **`send_email`** — send email via Gmail API with optional Excel attachment. Uses OpenExxon credentials.

**Typical agent workflow for "run report and email as Excel":**
1. Agent calls `execute_sql` to get the data
2. Agent calls `create_excel` with columns + rows → gets filepath
3. Agent calls `send_email` with filepath as `attachment_path`

