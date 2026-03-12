"""
file_generator.py — File generation tools for the Sealine Data Chat agent.

Provides three tool functions (generate_plot, generate_pdf, generate_excel)
that the Claude agent invokes to create downloadable files. Each function
generates a file, saves it to a temp directory, and returns metadata used
to emit SSE events to the React frontend.

Also exports FILE_TOOLS — the list of JSON tool definitions passed to the
Claude API — and a cleanup_expired_files utility for the background thread.

Dependencies:
    pip install matplotlib plotly openpyxl weasyprint reportlab
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

# Configure matplotlib for non-GUI (server) use BEFORE importing pyplot.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.colors as mcolors  # noqa: E402

import numpy as np  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions (JSON schemas passed to Claude API)
# ---------------------------------------------------------------------------

GENERATE_PLOT_TOOL: dict[str, Any] = {
    "name": "generate_plot",
    "description": (
        "Generate a chart or plot from data. Supports bar, line, scatter, pie, "
        "and heatmap chart types. Use matplotlib for static charts or plotly for "
        "interactive charts."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plot_type": {
                "type": "string",
                "enum": ["bar", "line", "scatter", "pie", "heatmap", "histogram"],
            },
            "title": {"type": "string", "description": "Chart title."},
            "data": {
                "type": "object",
                "description": (
                    "Chart data as JSON. For bar/line/histogram: "
                    '{"labels": [...], "values": [...]}. '
                    'For scatter: {"x": [...], "y": [...]}. '
                    'For pie: {"labels": [...], "values": [...]}. '
                    'For heatmap: {"labels_x": [...], "labels_y": [...], "values": [[...]]}'
                ),
            },
            "interactive": {
                "type": "boolean",
                "description": (
                    "If true, generate Plotly HTML. If false, generate matplotlib PNG."
                ),
                "default": False,
            },
            "x_label": {"type": "string", "description": "X-axis label (optional)."},
            "y_label": {"type": "string", "description": "Y-axis label (optional)."},
        },
        "required": ["plot_type", "title", "data"],
    },
}

GENERATE_PDF_TOOL: dict[str, Any] = {
    "name": "generate_pdf",
    "description": (
        "Generate a PDF report with a title, optional summary, and data table. "
        "Use when user asks for PDF or downloadable report."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "summary": {
                "type": "string",
                "description": "Optional summary paragraph above the table.",
            },
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "string"}},
            },
            "filename": {
                "type": "string",
                "description": "Output filename without extension.",
            },
        },
        "required": ["title", "columns", "rows"],
    },
}

GENERATE_EXCEL_TOOL: dict[str, Any] = {
    "name": "generate_excel",
    "description": (
        "Generate a formatted Excel (.xlsx) report with blue header row "
        "(#1F4788), frozen top row, and auto-sized columns."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Sheet name."},
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array"}},
            "filename": {
                "type": "string",
                "description": "Output filename without extension.",
            },
        },
        "required": ["title", "columns", "rows"],
    },
}

FILE_TOOLS: list[dict[str, Any]] = [
    GENERATE_PLOT_TOOL,
    GENERATE_PDF_TOOL,
    GENERATE_EXCEL_TOOL,
]

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

HEADER_COLOR_HEX = "#1F4788"  # Dark-blue header used for Excel & PDF
ALT_ROW_COLOR = "#F5F5F5"     # Light-gray alternating row background
MAX_COL_WIDTH = 50             # Excel column max width (chars)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short_uuid() -> str:
    """Return the first 8 characters of a UUID4 hex string."""
    return uuid.uuid4().hex[:8]


def _slugify(text: str) -> str:
    """Convert *text* to a filesystem-safe slug (lowercase, underscores)."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)   # strip special chars
    slug = re.sub(r"[\s-]+", "_", slug)     # spaces / dashes -> underscores
    slug = slug.strip("_")
    return slug[:80] or "file"              # cap length, fallback


def ensure_file_store(path: str) -> None:
    """Create the file-store directory if it doesn't already exist."""
    os.makedirs(path, exist_ok=True)


def _file_meta(
    file_id: str,
    display_name: str,
    file_type: str,
    file_path: str,
) -> dict[str, Any]:
    """Build the standard metadata dict returned by every generator."""
    size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    return {
        "file_id": file_id,
        "filename": display_name,
        "file_type": file_type,
        "file_path": file_path,
        "size_bytes": size,
    }


def _is_numeric(value: Any) -> bool:
    """Return True if *value* looks like a number (int, float, or numeric string)."""
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value.replace(",", ""))
            return True
        except (ValueError, AttributeError):
            return False
    return False


# ---------------------------------------------------------------------------
# 1. generate_plot
# ---------------------------------------------------------------------------


def generate_plot(
    plot_type: str,
    title: str,
    data: dict[str, Any],
    interactive: bool = False,
    x_label: str | None = None,
    y_label: str | None = None,
    file_store_path: str = "./tmp/files",
) -> dict[str, Any]:
    """Generate a chart and save it to *file_store_path*.

    Returns a metadata dict consumed by the SSE layer.
    """
    ensure_file_store(file_store_path)
    file_id = _short_uuid()
    title_slug = _slugify(title)

    try:
        if interactive:
            return _plot_interactive(
                plot_type, title, data, x_label, y_label,
                file_id, title_slug, file_store_path,
            )
        return _plot_static(
            plot_type, title, data, x_label, y_label,
            file_id, title_slug, file_store_path,
        )
    except Exception as exc:
        logger.exception("generate_plot failed")
        return {"error": str(exc)}


# -- static (matplotlib) ---------------------------------------------------


def _plot_static(
    plot_type: str,
    title: str,
    data: dict[str, Any],
    x_label: str | None,
    y_label: str | None,
    file_id: str,
    title_slug: str,
    file_store_path: str,
) -> dict[str, Any]:
    """Render a chart with matplotlib and save as PNG."""

    # Use a clean built-in style.  seaborn-v0_8-whitegrid ships with
    # matplotlib >= 3.6.  Fall back gracefully for older versions.
    try:
        plt.style.use("seaborn-v0_8-whitegrid")
    except OSError:
        try:
            plt.style.use("seaborn-whitegrid")
        except OSError:
            pass  # use default style

    fig, ax = plt.subplots(figsize=(10, 6))

    labels = data.get("labels", [])
    values = data.get("values", [])

    if plot_type == "bar":
        x_positions = range(len(labels))
        ax.bar(x_positions, values, color=HEADER_COLOR_HEX, edgecolor="white")
        ax.set_xticks(list(x_positions))
        ax.set_xticklabels(labels, rotation=45, ha="right")

    elif plot_type == "line":
        ax.plot(labels, values, marker="o", linewidth=2, color=HEADER_COLOR_HEX)
        ax.tick_params(axis="x", rotation=45)

    elif plot_type == "scatter":
        x = data.get("x", [])
        y = data.get("y", [])
        ax.scatter(x, y, alpha=0.7, color=HEADER_COLOR_HEX, edgecolors="white")

    elif plot_type == "pie":
        # Pie uses fig-level; close the original figure to prevent leak,
        # then create a fresh one.
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(10, 6))
        wedges, texts, autotexts = ax.pie(
            values,
            labels=labels,
            autopct="%1.1f%%",
            startangle=140,
            textprops={"fontsize": 10},
        )
        ax.set_aspect("equal")

    elif plot_type == "heatmap":
        labels_x = data.get("labels_x", [])
        labels_y = data.get("labels_y", [])
        matrix = np.array(values, dtype=float)
        im = ax.imshow(matrix, cmap="Blues", aspect="auto")
        ax.set_xticks(range(len(labels_x)))
        ax.set_xticklabels(labels_x, rotation=45, ha="right")
        ax.set_yticks(range(len(labels_y)))
        ax.set_yticklabels(labels_y)
        fig.colorbar(im, ax=ax)

    elif plot_type == "histogram":
        ax.hist(values, bins="auto", color=HEADER_COLOR_HEX, edgecolor="white")

    else:
        plt.close(fig)
        return {"error": f"Unsupported plot_type: {plot_type}"}

    ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
    if x_label:
        ax.set_xlabel(x_label)
    if y_label:
        ax.set_ylabel(y_label)

    fig.tight_layout()

    filename = f"{file_id}_{title_slug}.png"
    full_path = os.path.join(file_store_path, filename)
    fig.savefig(full_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return _file_meta(file_id, f"{title_slug}.png", "image/png", full_path)


# -- interactive (plotly) ---------------------------------------------------


def _plot_interactive(
    plot_type: str,
    title: str,
    data: dict[str, Any],
    x_label: str | None,
    y_label: str | None,
    file_id: str,
    title_slug: str,
    file_store_path: str,
) -> dict[str, Any]:
    """Render a chart with Plotly and save as self-contained HTML."""

    try:
        import plotly.graph_objects as go  # type: ignore[import-untyped]
    except ImportError:
        return {"error": "plotly is not installed. Run: pip install plotly"}

    labels = data.get("labels", [])
    values = data.get("values", [])
    fig: go.Figure | None = None

    if plot_type == "bar":
        fig = go.Figure(
            data=[go.Bar(x=labels, y=values, marker_color=HEADER_COLOR_HEX)]
        )

    elif plot_type == "line":
        fig = go.Figure(
            data=[go.Scatter(x=labels, y=values, mode="lines+markers",
                             line=dict(color=HEADER_COLOR_HEX))]
        )

    elif plot_type == "scatter":
        x = data.get("x", [])
        y = data.get("y", [])
        fig = go.Figure(
            data=[go.Scatter(x=x, y=y, mode="markers",
                             marker=dict(color=HEADER_COLOR_HEX, opacity=0.7))]
        )

    elif plot_type == "pie":
        fig = go.Figure(
            data=[go.Pie(labels=labels, values=values, hole=0)]
        )

    elif plot_type == "heatmap":
        labels_x = data.get("labels_x", [])
        labels_y = data.get("labels_y", [])
        fig = go.Figure(
            data=[go.Heatmap(z=values, x=labels_x, y=labels_y,
                             colorscale="Blues")]
        )

    elif plot_type == "histogram":
        fig = go.Figure(
            data=[go.Histogram(x=values, marker_color=HEADER_COLOR_HEX)]
        )

    else:
        return {"error": f"Unsupported plot_type: {plot_type}"}

    fig.update_layout(
        title=dict(text=title, font=dict(size=16)),
        xaxis_title=x_label or "",
        yaxis_title=y_label or "",
        template="plotly_white",
        margin=dict(l=60, r=30, t=60, b=60),
    )

    filename = f"{file_id}_{title_slug}.html"
    full_path = os.path.join(file_store_path, filename)
    fig.write_html(full_path, include_plotlyjs=True)

    return _file_meta(file_id, f"{title_slug}.html", "text/html", full_path)


# ---------------------------------------------------------------------------
# 2. generate_pdf
# ---------------------------------------------------------------------------

_PDF_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  @page {{
    size: A4 landscape;
    margin: 20mm 15mm 25mm 15mm;
    @bottom-center {{
      content: "Page " counter(page) " of " counter(pages);
      font-size: 9px;
      color: #888;
    }}
  }}
  body {{
    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    font-size: 11px;
    color: #222;
    line-height: 1.4;
  }}
  .report-header {{
    margin-bottom: 18px;
  }}
  .report-header h1 {{
    font-size: 22px;
    color: {header_color};
    margin: 0 0 4px 0;
  }}
  .report-header .timestamp {{
    font-size: 10px;
    color: #888;
  }}
  .summary {{
    margin-bottom: 16px;
    font-size: 12px;
    color: #444;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 10px;
  }}
  th {{
    background-color: {header_color};
    color: #fff;
    font-weight: bold;
    text-align: left;
    padding: 6px 8px;
    border: 1px solid {header_color};
  }}
  td {{
    padding: 5px 8px;
    border: 1px solid #ddd;
  }}
  tr:nth-child(even) td {{
    background-color: {alt_row};
  }}
  td.num {{
    text-align: right;
    font-variant-numeric: tabular-nums;
  }}
</style>
</head>
<body>
  <div class="report-header">
    <h1>{title}</h1>
    <div class="timestamp">Generated {timestamp}</div>
  </div>
  {summary_html}
  <table>
    <thead><tr>{header_cells}</tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
</body>
</html>
"""


def generate_pdf(
    title: str,
    columns: list[str],
    rows: list[list[str]],
    summary: str | None = None,
    filename: str | None = None,
    file_store_path: str = "./tmp/files",
) -> dict[str, Any]:
    """Generate a PDF report and save it to *file_store_path*.

    Attempts to use WeasyPrint for high-quality HTML->PDF conversion.
    Falls back to ReportLab if WeasyPrint is unavailable.
    """
    ensure_file_store(file_store_path)
    file_id = _short_uuid()
    safe_name = _slugify(filename) if filename else _slugify(title)

    try:
        return _pdf_weasyprint(
            title, columns, rows, summary, file_id, safe_name, file_store_path,
        )
    except ImportError:
        logger.info("WeasyPrint not available, falling back to ReportLab")
        try:
            return _pdf_reportlab(
                title, columns, rows, summary, file_id, safe_name, file_store_path,
            )
        except ImportError:
            return {
                "error": (
                    "Neither weasyprint nor reportlab is installed. "
                    "Install one of them: pip install weasyprint  or  pip install reportlab"
                )
            }
    except Exception as exc:
        logger.exception("generate_pdf failed")
        return {"error": str(exc)}


def _pdf_weasyprint(
    title: str,
    columns: list[str],
    rows: list[list[str]],
    summary: str | None,
    file_id: str,
    safe_name: str,
    file_store_path: str,
) -> dict[str, Any]:
    """Render PDF with WeasyPrint (raises ImportError if not installed)."""
    from weasyprint import HTML  # type: ignore[import-untyped]

    # Detect which columns are numeric (by checking first data row).
    numeric_cols: set[int] = set()
    if rows:
        for idx, val in enumerate(rows[0]):
            if _is_numeric(val):
                numeric_cols.add(idx)

    header_cells = "".join(f"<th>{_esc(c)}</th>" for c in columns)

    body_parts: list[str] = []
    for row in rows:
        cells = []
        for idx, val in enumerate(row):
            cls = ' class="num"' if idx in numeric_cols else ""
            cells.append(f"<td{cls}>{_esc(str(val))}</td>")
        body_parts.append(f"<tr>{''.join(cells)}</tr>")

    summary_html = f'<div class="summary">{_esc(summary)}</div>' if summary else ""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html_str = _PDF_HTML_TEMPLATE.format(
        header_color=HEADER_COLOR_HEX,
        alt_row=ALT_ROW_COLOR,
        title=_esc(title),
        timestamp=timestamp,
        summary_html=summary_html,
        header_cells=header_cells,
        body_rows="\n".join(body_parts),
    )

    out_filename = f"{file_id}_{safe_name}.pdf"
    full_path = os.path.join(file_store_path, out_filename)
    HTML(string=html_str).write_pdf(full_path)

    return _file_meta(file_id, f"{safe_name}.pdf", "application/pdf", full_path)


def _pdf_reportlab(
    title: str,
    columns: list[str],
    rows: list[list[str]],
    summary: str | None,
    file_id: str,
    safe_name: str,
    file_store_path: str,
) -> dict[str, Any]:
    """Fallback PDF generation using ReportLab (raises ImportError if absent)."""
    from reportlab.lib import colors as rl_colors  # type: ignore[import-untyped]
    from reportlab.lib.pagesizes import A4, landscape  # type: ignore[import-untyped]
    from reportlab.lib.styles import getSampleStyleSheet  # type: ignore[import-untyped]
    from reportlab.lib.units import mm  # type: ignore[import-untyped]
    from reportlab.platypus import (  # type: ignore[import-untyped]
        SimpleDocTemplate,
        Table,
        TableStyle,
        Paragraph,
        Spacer,
    )

    out_filename = f"{file_id}_{safe_name}.pdf"
    full_path = os.path.join(file_store_path, out_filename)

    doc = SimpleDocTemplate(
        full_path,
        pagesize=landscape(A4),
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=20 * mm,
        bottomMargin=25 * mm,
    )

    styles = getSampleStyleSheet()
    elements: list[Any] = []

    # Title
    elements.append(Paragraph(title, styles["Title"]))
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    elements.append(Paragraph(f"Generated {timestamp}", styles["Normal"]))
    elements.append(Spacer(1, 6 * mm))

    # Summary
    if summary:
        elements.append(Paragraph(summary, styles["Normal"]))
        elements.append(Spacer(1, 4 * mm))

    # Table
    table_data = [columns] + [[str(v) for v in row] for row in rows]
    tbl = Table(table_data, repeatRows=1)

    # Derive header RGB from hex
    hr, hg, hb = (
        int(HEADER_COLOR_HEX[1:3], 16) / 255.0,
        int(HEADER_COLOR_HEX[3:5], 16) / 255.0,
        int(HEADER_COLOR_HEX[5:7], 16) / 255.0,
    )
    header_bg = rl_colors.Color(hr, hg, hb)

    style_commands: list[Any] = [
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, rl_colors.Color(0.8, 0.8, 0.8)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    # Alternating row colors
    for i in range(1, len(table_data)):
        if i % 2 == 0:
            style_commands.append(
                ("BACKGROUND", (0, i), (-1, i), rl_colors.Color(0.96, 0.96, 0.96))
            )

    tbl.setStyle(TableStyle(style_commands))
    elements.append(tbl)
    doc.build(elements)

    return _file_meta(file_id, f"{safe_name}.pdf", "application/pdf", full_path)


def _esc(text: str) -> str:
    """Minimal HTML escaping for template interpolation."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# 3. generate_excel
# ---------------------------------------------------------------------------


def generate_excel(
    title: str,
    columns: list[str],
    rows: list[list[Any]],
    filename: str | None = None,
    file_store_path: str = "./tmp/files",
) -> dict[str, Any]:
    """Generate a formatted .xlsx workbook and save it to *file_store_path*.

    Styling matches user preferences from MEMORY.md:
      - Header: #1F4788 background, white bold text, font size 11
      - Freeze pane at row 1
      - Auto-sized columns (max 50 chars)
    """
    ensure_file_store(file_store_path)
    file_id = _short_uuid()
    safe_name = _slugify(filename) if filename else _slugify(title)

    try:
        from openpyxl import Workbook  # type: ignore[import-untyped]
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side  # type: ignore[import-untyped]
        from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]
    except ImportError:
        return {"error": "openpyxl is not installed. Run: pip install openpyxl"}

    try:
        wb = Workbook()
        ws = wb.active

        # Sheet name — Excel limits to 31 characters.
        ws.title = title[:31]

        # ── Header styling ────────────────────────────────────────────
        header_fill = PatternFill(
            start_color="1F4788", end_color="1F4788", fill_type="solid"
        )
        header_font = Font(
            name="Calibri", size=11, bold=True, color="FFFFFF"
        )
        header_alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )
        thin_border = Border(
            left=Side(style="thin", color="CCCCCC"),
            right=Side(style="thin", color="CCCCCC"),
            top=Side(style="thin", color="CCCCCC"),
            bottom=Side(style="thin", color="CCCCCC"),
        )

        # ── Write header row ──────────────────────────────────────────
        for col_idx, col_name in enumerate(columns, start=1):
            cell = ws.cell(row=1, column=col_idx, value=col_name)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
            cell.border = thin_border

        # ── Data styling ──────────────────────────────────────────────
        data_font = Font(name="Calibri", size=11)
        alt_fill = PatternFill(
            start_color="F5F5F5", end_color="F5F5F5", fill_type="solid"
        )
        data_alignment = Alignment(vertical="center", wrap_text=False)

        # ── Write data rows ───────────────────────────────────────────
        for row_idx, row_data in enumerate(rows, start=2):
            for col_idx, value in enumerate(row_data, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.font = data_font
                cell.alignment = data_alignment
                cell.border = thin_border
                # Alternating row colors (even data rows = row_idx 3, 5, ...)
                if row_idx % 2 == 0:
                    cell.fill = alt_fill

        # ── Freeze top row ────────────────────────────────────────────
        ws.freeze_panes = "A2"

        # ── Auto-size columns ─────────────────────────────────────────
        for col_idx in range(1, len(columns) + 1):
            max_length = 0
            col_letter = get_column_letter(col_idx)
            for row_cells in ws.iter_rows(
                min_col=col_idx, max_col=col_idx,
                min_row=1, max_row=ws.max_row,
            ):
                for cell in row_cells:
                    val = str(cell.value) if cell.value is not None else ""
                    max_length = max(max_length, len(val))
            # Add a small buffer; cap at MAX_COL_WIDTH
            adjusted = min(max_length + 3, MAX_COL_WIDTH)
            ws.column_dimensions[col_letter].width = adjusted

        # ── Save ──────────────────────────────────────────────────────
        out_filename = f"{file_id}_{safe_name}.xlsx"
        full_path = os.path.join(file_store_path, out_filename)
        wb.save(full_path)

        return _file_meta(file_id, f"{safe_name}.xlsx",
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                          full_path)

    except Exception as exc:
        logger.exception("generate_excel failed")
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# 4. cleanup_expired_files
# ---------------------------------------------------------------------------


def cleanup_expired_files(
    file_store_path: str = "./tmp/files",
    ttl_hours: int = 24,
) -> int:
    """Delete files older than *ttl_hours* from *file_store_path*.

    Returns the number of deleted files.
    """
    if not os.path.isdir(file_store_path):
        return 0

    now = time.time()
    cutoff = now - (ttl_hours * 3600)
    deleted = 0

    for entry in os.scandir(file_store_path):
        if entry.is_file():
            try:
                if entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
                    deleted += 1
                    logger.debug("Deleted expired file: %s", entry.name)
            except OSError as exc:
                logger.warning("Could not delete %s: %s", entry.path, exc)

    if deleted:
        logger.info(
            "Cleaned up %d expired file(s) from %s", deleted, file_store_path,
        )
    return deleted


# ---------------------------------------------------------------------------
# Convenience: dispatch a tool call by name
# ---------------------------------------------------------------------------


def handle_file_tool(tool_name: str, tool_input: dict[str, Any],
                     file_store_path: str = "./tmp/files") -> dict[str, Any]:
    """Dispatch a tool call to the appropriate generator function.

    This helper is used by the agent loop so it doesn't need to maintain
    its own if/elif chain for file-generation tools.
    """
    if tool_name == "generate_plot":
        return generate_plot(
            plot_type=tool_input["plot_type"],
            title=tool_input["title"],
            data=tool_input["data"],
            interactive=tool_input.get("interactive", False),
            x_label=tool_input.get("x_label"),
            y_label=tool_input.get("y_label"),
            file_store_path=file_store_path,
        )
    elif tool_name == "generate_pdf":
        return generate_pdf(
            title=tool_input["title"],
            columns=tool_input["columns"],
            rows=tool_input["rows"],
            summary=tool_input.get("summary"),
            filename=tool_input.get("filename"),
            file_store_path=file_store_path,
        )
    elif tool_name == "generate_excel":
        return generate_excel(
            title=tool_input["title"],
            columns=tool_input["columns"],
            rows=tool_input["rows"],
            filename=tool_input.get("filename"),
            file_store_path=file_store_path,
        )
    else:
        return {"error": f"Unknown file tool: {tool_name}"}
