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
        "Generate a chart or plot from data. Supports bar, bar_stacked (grouped series), line, scatter, pie, "
        "heatmap, histogram, and map (interactive geographic map with OpenStreetMap "
        "tiles) chart types. ALWAYS use plot_type='map' with interactive=true when "
        "displaying geographic/location data with latitude and longitude coordinates. "
        "Use matplotlib for static charts or Leaflet.js for interactive maps / Plotly for interactive charts."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plot_type": {
                "type": "string",
                "enum": ["bar", "bar_stacked", "line", "scatter", "pie", "heatmap", "histogram", "map"],
                "description": (
                    "Chart type. Use 'map' for any geographic/location data with "
                    "lat/lon coordinates — it renders an interactive Leaflet map. "
                    "Use 'bar_stacked' for stacked/grouped bar charts with multiple series. "
                    "Other types: bar, line, scatter, pie, heatmap, histogram."
                ),
            },
            "title": {"type": "string", "description": "Chart title."},
            "data": {
                "type": "object",
                "description": (
                    "Chart data as JSON. "
                    'For bar/line/histogram: {"labels": [...], "values": [...]}. '
                    'For bar_stacked: {"labels": [...], "series": [{"name": "SeriesA", "values": [...]}, {"name": "SeriesB", "values": [...]}]}. '
                    'For scatter: {"x": [...], "y": [...]}. '
                    'For pie: {"labels": [...], "values": [...]}. '
                    'For heatmap: {"labels_x": [...], "labels_y": [...], "values": [[...]]}. '
                    'For map with a SINGLE route: {"lat": [...], "lon": [...], "labels": [...], '
                    '"values": [...], "sizes": [...], "arrows": true/false, '
                    '"connections": [[from_idx, to_idx], ...]} '
                    'where labels are hover text (e.g. container ID), '
                    'values are optional numeric values shown on hover, '
                    '"arrows": true auto-connects points in sequence with directional arrows, '
                    '"connections" explicitly defines which point pairs to connect with arrows '
                    '(e.g. [[0,1],[1,2]] draws arrows from point 0→1 and 1→2). '
                    'For map with MULTIPLE containers/routes — ALWAYS use the GROUPS format. '
                    'Pass flat parallel arrays with a "groups" key; Python auto-groups them into separate coloured routes: '
                    '{"lat": [lat1,lat2,...], "lon": [lon1,lon2,...], '
                    '"labels": ["label1","label2",...], '
                    '"groups": ["CONTAINER_A","CONTAINER_A","CONTAINER_B","CONTAINER_B",...], '
                    '"arrows": true} '
                    'All points with the same group name form one coloured route with arrows. '
                    'Each distinct group value gets a distinct colour automatically. '
                    'DO NOT build nested route objects — use flat arrays + groups instead. '
                    '"labels" per point are popup text shown on click. '
                    'To highlight geographic zones (e.g. war zones, risk areas) as '
                    'filled polygon overlays, add a "zones" key: '
                    '{"lat": [...], "lon": [...], "labels": [...], '
                    '"zones": [{"name": "Red Sea War Zone", '
                    '"lat": [lat1, lat2, ...], "lon": [lon1, lon2, ...], '
                    '"color": "rgba(255,0,0,0.25)"}]} '
                    'Each zone is a closed polygon — repeat the first point at the end '
                    'to close the shape. Zones are rendered as semi-transparent fills. '
                    'To highlight countries or world regions by name, add '
                    '"highlight_regions": [{"name": "China", "color": "rgba(255,0,0,0.25)"}, '
                    '{"name": "United States", "color": "rgba(31,71,136,0.25)"}] '
                    'to the map data. Country names are matched case-insensitively. '
                    'Also supports ISO alpha-2 codes (e.g. "CN", "US", "DE"). '
                    'To render bubble maps where marker size reflects a numeric value, '
                    'include "sizes": [n1, n2, ...] — values are scaled to pixel radius.'
                ),
            },
            "interactive": {
                "type": "boolean",
                "description": (
                    "If true, generate Plotly HTML (required for map type). "
                    "If false, generate matplotlib PNG."
                ),
                "default": False,
            },
            "x_label": {"type": "string", "description": "X-axis label (optional, not used for map)."},
            "y_label": {"type": "string", "description": "Y-axis label (optional, not used for map)."},
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
            "rows": {"type": "array", "items": {"type": "array", "items": {}}},
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

    elif plot_type == "bar_stacked":
        series_list = data.get("series", [])
        x_pos = range(len(labels))
        bottom = [0.0] * len(labels)
        PALETTE = [
            "#1F4788", "#E07B39", "#2ECC71", "#E74C3C", "#9B59B6",
            "#F39C12", "#1ABC9C", "#E91E63", "#00BCD4", "#8BC34A",
        ]
        for idx, s in enumerate(series_list):
            s_vals = s.get("values", [0] * len(labels))
            ax.bar(
                x_pos, s_vals, bottom=bottom,
                label=s.get("name", f"Series {idx+1}"),
                color=PALETTE[idx % len(PALETTE)],
                edgecolor="white",
            )
            bottom = [b + v for b, v in zip(bottom, s_vals)]
        ax.set_xticks(list(x_pos))
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.legend(fontsize=9)

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


# ---------------------------------------------------------------------------
# Leaflet map HTML template (placeholders: |||TITLE|||, |||DATA_JSON|||,
# |||CENTER_LAT|||, |||CENTER_LON|||, |||ZOOM|||)
# ---------------------------------------------------------------------------
_LEAFLET_MAP_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>|||TITLE|||</title>
<script>
window.onerror=function(m,s,l,c,e){
  var el=document.getElementById('map-error');
  if(el){el.style.display='block';el.innerHTML='<b>JS Error:</b> '+m+' (line '+l+')';}
  else if(document.body){document.body.style.background='#fff';document.body.innerHTML='<pre style="color:red;padding:20px">JS Error: '+m+'\\nLine: '+l+'</pre>';}
  else{setTimeout(function(){document.body&&(document.body.style.background='#fff',document.body.innerHTML='<pre style="color:red;padding:20px">JS Error: '+m+'\\nLine: '+l+'</pre>');},200);}
  return false;
};
</script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/topojson-client@3/dist/topojson-client.min.js"></script>
<style>
  html, body, #map { margin: 0; padding: 0; width: 100%; height: 100vh; overflow: hidden; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
  .map-title {
    position: absolute; top: 12px; left: 50%; transform: translateX(-50%);
    z-index: 1000; background: rgba(255,255,255,0.95);
    padding: 7px 20px; border-radius: 8px;
    font-size: 15px; font-weight: 700; color: #1F4788;
    box-shadow: 0 2px 10px rgba(0,0,0,0.18);
    white-space: nowrap; pointer-events: none;
  }
  .legend {
    background: rgba(255,255,255,0.95); border-radius: 8px;
    padding: 10px 14px; box-shadow: 0 2px 10px rgba(0,0,0,0.18);
    max-height: 280px; overflow-y: auto; min-width: 150px;
  }
  .legend h4 {
    margin: 0 0 8px 0; font-size: 11px; font-weight: 700;
    color: #555; text-transform: uppercase; letter-spacing: 0.6px;
  }
  .legend-item { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 12px; color: #333; }
  .leg-line { width: 22px; height: 3px; border-radius: 2px; flex-shrink: 0; }
  .leg-dot { width: 11px; height: 11px; border-radius: 50%; border: 2px solid rgba(255,255,255,0.8); flex-shrink: 0; }
  .leg-zone { width: 14px; height: 10px; border-radius: 3px; flex-shrink: 0; }
  .stop-label {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    font-size: 11px; font-weight: 600;
    color: #1a1a1a;
    white-space: nowrap;
    text-shadow: 1px 1px 0 #fff, -1px -1px 0 #fff, 1px -1px 0 #fff, -1px 1px 0 #fff;
    pointer-events: none;
  }
  .leaflet-popup-content { font-size: 13px; line-height: 1.6; min-width: 160px; }
</style>
</head>
<body>
<div class="map-title">|||TITLE|||</div>
<div id="map"></div>
<div id="map-error" style="display:none;position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  background:#fff;padding:20px;border:2px solid red;border-radius:8px;z-index:9999;
  font-family:monospace;font-size:13px;max-width:80%;word-break:break-all;"></div>
<script>
try {
// ── Data ─────────────────────────────────────────────────────────────────
const MAP_DATA = |||DATA_JSON|||;

// ── Country name → ISO numeric code mapping ───────────────────────────────
const COUNTRY_CODES = {
  'afghanistan':4,'albania':8,'algeria':12,'angola':24,'argentina':32,
  'australia':36,'austria':40,'bahrain':48,'bangladesh':50,'belgium':56,
  'bolivia':68,'brazil':76,'bulgaria':100,'cambodia':116,'cameroon':120,
  'canada':124,'chile':152,'china':156,'prc':156,'cn':156,'colombia':170,
  'congo':180,'costa rica':188,'croatia':191,'cuba':192,'czech republic':203,
  'czechia':203,'denmark':208,'dk':208,'dominican republic':214,
  'ecuador':218,'egypt':818,'eg':818,'ethiopia':231,'finland':246,'fi':246,
  'france':250,'fr':250,'germany':276,'de':276,'ghana':288,'greece':300,
  'gr':300,'hong kong':344,'hk':344,'hungary':348,'india':356,'in':356,
  'indonesia':360,'id':360,'iran':364,'iraq':368,'ireland':372,'israel':376,
  'il':376,'italy':380,'it':380,'jamaica':388,'japan':392,'jp':392,
  'jordan':400,'kenya':404,'south korea':410,'korea':410,'kr':410,
  'kuwait':414,'kw':414,'laos':418,'latvia':428,'libya':434,'malaysia':458,
  'my':458,'mexico':484,'mx':484,'morocco':504,'ma':504,'mozambique':508,
  'myanmar':104,'burma':104,'mm':104,'netherlands':528,'nl':528,'holland':528,
  'new zealand':554,'nz':554,'nicaragua':558,'nigeria':566,'ng':566,
  'norway':578,'no':578,'oman':512,'om':512,'pakistan':586,'pk':586,
  'panama':591,'pa':591,'peru':604,'philippines':608,'ph':608,'poland':616,
  'pl':616,'portugal':620,'pt':620,'qatar':634,'qa':634,'romania':642,
  'russia':643,'ru':643,'saudi arabia':682,'sa':682,'senegal':686,
  'singapore':702,'sg':702,'somalia':706,'south africa':710,'za':710,
  'spain':724,'es':724,'sri lanka':144,'lk':144,'sudan':729,'sd':729,
  'sweden':752,'se':752,'switzerland':756,'ch':756,'taiwan':158,'tw':158,
  'tanzania':834,'tz':834,'thailand':764,'th':764,'tunisia':788,'tn':788,
  'turkey':792,'tr':792,'ukraine':804,'ua':804,'united arab emirates':784,
  'uae':784,'ae':784,'united kingdom':826,'uk':826,'gb':826,'britain':826,
  'united states':840,'usa':840,'us':840,'america':840,'uruguay':858,
  'venezuela':862,'vietnam':704,'vn':704,'yemen':887,'ye':887,
  'zambia':894,'zimbabwe':716
};

// ── Init map — CartoDB Voyager (English labels everywhere) ────────────────
if (!window.L) throw new Error('Leaflet not loaded — window.L is ' + typeof window.L);
const map = L.map('map', { zoomControl: true, scrollWheelZoom: true })
             .setView([|||CENTER_LAT|||, |||CENTER_LON|||], |||ZOOM|||);
// Force re-layout after iframe finishes sizing (fixes blank-map-in-iframe bug)
// Call multiple times to cover browsers that finish sizing at different times
[100, 300, 600, 1200].forEach(function(ms) {
  setTimeout(function() { map.invalidateSize(false); }, ms);
});
window.addEventListener('resize', function() { map.invalidateSize(false); });

L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
  subdomains: 'abcd', maxZoom: 19
}).addTo(map);

// ── Helpers ───────────────────────────────────────────────────────────────
function parseRgba(str) {
  if (!str) return { r: 31, g: 71, b: 136, a: 0.25 };
  const m = str.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?\\)/);
  if (m) return { r: +m[1], g: +m[2], b: +m[3], a: m[4] !== undefined ? +m[4] : 1 };
  if (str[0] === '#') {
    const h = str.slice(1);
    return { r: parseInt(h.slice(0,2),16), g: parseInt(h.slice(2,4),16), b: parseInt(h.slice(4,6),16), a: 1 };
  }
  return { r: 31, g: 71, b: 136, a: 0.25 };
}
function toHex(r,g,b) {
  return '#' + [r,g,b].map(v => v.toString(16).padStart(2,'0')).join('');
}

// ── Pure-Leaflet arrow line (no plugin needed) ────────────────────────────
function drawArrowLine(fromPt, toPt, color) {
  // Line
  L.polyline([fromPt, toPt], { color: color, weight: 3, opacity: 0.85 }).addTo(map);

  // Arrow at midpoint via SVG DivIcon
  const midLat = (fromPt[0] + toPt[0]) / 2;
  const midLon = (fromPt[1] + toPt[1]) / 2;
  const dLat = toPt[0] - fromPt[0];
  const dLon = toPt[1] - fromPt[1];
  // bearing: 0° = north, clockwise
  const angle = Math.atan2(dLon, dLat) * 180 / Math.PI;
  const svg = '<svg width="18" height="18" viewBox="-9 -9 18 18" xmlns="http://www.w3.org/2000/svg">'
    + '<polygon points="0,-7 5,3 0,0 -5,3" fill="' + color + '" opacity="0.95"'
    + ' transform="rotate(' + angle.toFixed(1) + ')"/></svg>';
  L.marker([midLat, midLon], {
    icon: L.divIcon({ html: svg, className: '', iconSize: [18, 18], iconAnchor: [9, 9] }),
    interactive: false,
    zIndexOffset: 100,
  }).addTo(map);
}

// ── Legend ────────────────────────────────────────────────────────────────
const legendCtrl = L.control({ position: 'bottomright' });
legendCtrl.onAdd = function() {
  const div = L.DomUtil.create('div', 'legend');
  let html = '<h4>Legend</h4>';
  MAP_DATA.routes.forEach(r => {
    if (!r.name) return;
    const hasLines = r.connections && r.connections.length > 0;
    html += '<div class="legend-item">';
    if (hasLines) html += '<div class="leg-line" style="background:' + r.color + '"></div>';
    html += '<div class="leg-dot" style="background:' + r.color + '"></div>';
    html += '<span>' + r.name + '</span></div>';
  });
  MAP_DATA.zones.forEach(z => {
    const c = parseRgba(z.color);
    const hex = toHex(c.r, c.g, c.b);
    html += '<div class="legend-item"><div class="leg-zone" style="background:' + hex
          + ';opacity:0.55;border:2px solid ' + hex + '"></div><span>' + z.name + '</span></div>';
  });
  (MAP_DATA.highlight_regions || []).forEach(r => {
    const c = parseRgba(r.color);
    const hex = toHex(c.r, c.g, c.b);
    html += '<div class="legend-item"><div class="leg-zone" style="background:' + hex
          + ';opacity:0.55;border:2px solid ' + hex + '"></div><span>' + r.name + '</span></div>';
  });
  div.innerHTML = html;
  return div;
};
legendCtrl.addTo(map);

// ── Zone polygons ──────────────────────────────────────────────────────────
MAP_DATA.zones.forEach(function(zone) {
  const c = parseRgba(zone.color);
  const hex = toHex(c.r, c.g, c.b);
  L.polygon(zone.latlngs, {
    color: hex, weight: 2, opacity: 0.85,
    fillColor: hex, fillOpacity: c.a,
  }).addTo(map).bindTooltip('<b>' + zone.name + '</b>', { sticky: true });
});

// ── Country / region highlights (TopoJSON world atlas) ────────────────────
const highlightRegions = MAP_DATA.highlight_regions || [];
if (highlightRegions.length > 0) {
  const codeColorMap = {};
  highlightRegions.forEach(function(region) {
    const key = (region.name || '').toLowerCase().trim();
    const numCode = COUNTRY_CODES[key];
    if (numCode !== undefined) {
      codeColorMap[numCode] = { color: region.color, name: region.name };
    }
  });
  fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json')
    .then(function(r) { return r.json(); })
    .then(function(world) {
      if (typeof topojson === 'undefined') return;
      const countries = topojson.feature(world, world.objects.countries);
      L.geoJSON(countries, {
        style: function(feature) {
          const match = codeColorMap[+feature.id];
          if (match) {
            const c = parseRgba(match.color);
            const hex = toHex(c.r, c.g, c.b);
            return { color: hex, weight: 1.5, opacity: 0.8, fillColor: hex, fillOpacity: c.a };
          }
          return { fillOpacity: 0, opacity: 0, weight: 0 };
        },
        onEachFeature: function(feature, layer) {
          const match = codeColorMap[+feature.id];
          if (match) layer.bindTooltip('<b>' + match.name + '</b>', { sticky: true });
        }
      }).addTo(map);
    }).catch(function() {});
}

// ── Routes — lines + arrows + labelled dots ───────────────────────────────
MAP_DATA.routes.forEach(function(route) {
  const pts = route.points;        // [[lat, lon], ...]
  const color = route.color;
  const connections = route.connections || [];
  const sizes = route.sizes || [];
  const labels = route.labels || [];

  if (!pts || pts.length === 0) return;

  // 1. Draw lines with arrow markers
  connections.forEach(function(conn) {
    const fi = conn[0], ti = conn[1];
    if (fi >= pts.length || ti >= pts.length) return;
    drawArrowLine(pts[fi], pts[ti], color);
  });

  // 2. Draw stop markers + permanent labels
  pts.forEach(function(pt, i) {
    const rawLabel = (labels[i] || '').toString();
    // Normalise newlines and <br> tags to a standard <br>
    const normLabel = rawLabel.replace(/\\n/g, '<br>').replace(/<br\\s*\\/?>/gi, '<br>');
    // First line only for the always-visible dot label (uncluttered)
    const firstLine = normLabel.split('<br>')[0].replace(/<[^>]+>/g, '').trim();
    // Full label with <br> rendered as line-breaks for the click popup
    const popupHtml = normLabel.replace(/<br>/g, '<br>');
    const radius = (sizes[i] && +sizes[i] > 0) ? Math.max(6, Math.min(30, +sizes[i])) : 10;

    // Filled circle marker
    const marker = L.circleMarker(pt, {
      radius: radius,
      color: '#ffffff', weight: 2,
      fillColor: color, fillOpacity: 1,
    }).addTo(map);

    // Popup on click — shows full detail (type, location, date, actual/estimated)
    if (popupHtml) {
      marker.bindPopup('<div style="font-size:13px;line-height:1.8">' + popupHtml + '</div>');
    }

    // Permanent label above the dot — first line only (keeps the map uncluttered)
    if (firstLine) {
      const labelIcon = L.divIcon({
        html: '<div class="stop-label">' + firstLine + '</div>',
        className: '',
        iconAnchor: [0, radius + 2],
      });
      L.marker(pt, { icon: labelIcon, interactive: false, zIndexOffset: 200 }).addTo(map);
    }
  });
});

// ── Fit bounds ─────────────────────────────────────────────────────────────
const allPts = [
  ...(MAP_DATA.routes || []).flatMap(function(r) { return r.points || []; }),
  ...(MAP_DATA.zones  || []).flatMap(function(z) { return z.latlngs || []; }),
];
if (allPts.length > 0) {
  try { map.fitBounds(L.latLngBounds(allPts), { padding: [60, 60], maxZoom: 12 }); }
  catch(e) {}
}
} catch(e) {
  var d = document.getElementById('map-error');
  if (d) { d.style.display = 'block'; d.innerHTML = '<b>Map Error:</b> ' + e.message + '<br><pre>' + e.stack + '</pre>'; }
}
</script>
</body>
</html>
"""


def _plot_leaflet_map(
    title: str,
    data: dict[str, Any],
    file_id: str,
    title_slug: str,
    file_store_path: str,
) -> dict[str, Any]:
    """Generate a Leaflet.js interactive map as a self-contained HTML file.

    Supports: markers, bubbles (sized circle markers), route polylines,
    directional arrow decorators, and filled polygon zone overlays.
    """
    import json as _json

    MAX_MAP_POINTS = 1000
    map_truncated = False

    ROUTE_COLORS = [
        "#E07B39", "#1F4788", "#2ECC71", "#E74C3C", "#9B59B6",
        "#F39C12", "#1ABC9C", "#E91E63", "#00BCD4", "#8BC34A",
    ]

    arrows = data.get("arrows", False)
    zones_raw = data.get("zones") or []

    # ── groups → routes conversion ────────────────────────────────────────
    # If caller passes flat lat/lon/labels + a "groups" array, auto-build
    # the routes list so they don't have to construct nested objects.
    groups_raw = data.get("groups")
    if groups_raw and not data.get("routes"):
        raw_lats   = [float(v) for v in (data.get("lat")    or [])]
        raw_lons   = [float(v) for v in (data.get("lon")    or [])]
        raw_labels = list(data.get("labels") or [str(i) for i in range(len(raw_lats))])
        raw_sizes  = list(data.get("sizes")  or [])
        # Preserve insertion order
        seen: dict[str, dict] = {}
        for idx, grp in enumerate(groups_raw):
            grp = str(grp)
            if grp not in seen:
                seen[grp] = {"lat": [], "lon": [], "labels": [], "sizes": []}
            if idx < len(raw_lats):
                seen[grp]["lat"].append(raw_lats[idx])
                seen[grp]["lon"].append(raw_lons[idx])
                seen[grp]["labels"].append(raw_labels[idx] if idx < len(raw_labels) else "")
                seen[grp]["sizes"].append(raw_sizes[idx] if idx < len(raw_sizes) else None)
        routes_raw = [
            {"name": grp, "lat": v["lat"], "lon": v["lon"],
             "labels": v["labels"], "sizes": [s for s in v["sizes"] if s is not None]}
            for grp, v in seen.items()
        ]
    else:
        routes_raw = data.get("routes")

    map_data: dict[str, Any] = {
        "title": title,
        "routes": [],
        "zones": [],
        "highlight_regions": [],
        "arrows": arrows,
    }

    all_lats: list[float] = []
    all_lons: list[float] = []

    # ── Zones ────────────────────────────────────────────────────────────
    for zone in zones_raw:
        z_lats = zone.get("lat", [])
        z_lons = zone.get("lon", [])
        if not z_lats or not z_lons:
            continue
        map_data["zones"].append({
            "name": zone.get("name", "Zone"),
            "latlngs": [[float(la), float(lo)] for la, lo in zip(z_lats, z_lons)],
            "color": zone.get("color", "rgba(255,0,0,0.25)"),
        })

    # ── Country / region highlights ──────────────────────────────────────
    for region in data.get("highlight_regions", []):
        map_data["highlight_regions"].append({
            "name": region.get("name", ""),
            "color": region.get("color", "rgba(31,71,136,0.25)"),
        })

    # ── Shared dedup helper ───────────────────────────────────────────────
    def _dedup_route(lats, lons, labels, sizes):
        """Collapse consecutive identical coordinates, keeping the longest label."""
        d_lats: list[float] = []
        d_lons: list[float] = []
        d_labels: list[str] = []
        d_sizes: list = []
        for idx, (la, lo) in enumerate(zip(lats, lons)):
            lbl = labels[idx] if idx < len(labels) else str(idx)
            sz  = sizes[idx]  if idx < len(sizes)  else None
            if d_lats and abs(la - d_lats[-1]) < 1e-6 and abs(lo - d_lons[-1]) < 1e-6:
                # Same location as previous — append new label as extra line
                d_labels[-1] = d_labels[-1] + "<br>" + lbl
            else:
                d_lats.append(la)
                d_lons.append(lo)
                d_labels.append(lbl)
                d_sizes.append(sz)
        return d_lats, d_lons, d_labels, [s for s in d_sizes if s is not None]

    # ── Routes ───────────────────────────────────────────────────────────
    if routes_raw:
        points_used = 0
        for i, route in enumerate(routes_raw):
            if points_used >= MAX_MAP_POINTS:
                map_truncated = True
                break
            r_lats = [float(v) for v in (route.get("lat") or [])]
            r_lons = [float(v) for v in (route.get("lon") or [])]
            r_labels = route.get("labels") or [str(j) for j in range(len(r_lats))]
            r_name = route.get("name", f"Route {i + 1}")
            r_connections = route.get("connections") or []
            r_sizes = route.get("sizes") or []

            remaining = MAX_MAP_POINTS - points_used
            if len(r_lats) > remaining:
                map_truncated = True
            r_lats = r_lats[:remaining]
            r_lons = r_lons[:remaining]
            r_labels = r_labels[:remaining]

            # Deduplicate consecutive identical coordinates within this route
            r_lats, r_lons, r_labels, r_sizes = _dedup_route(r_lats, r_lons, r_labels, r_sizes)

            # Rebuild connections for deduplicated points if not supplied
            if (arrows or r_connections) and len(r_lats) > 1:
                r_connections = [[j, j + 1] for j in range(len(r_lats) - 1)]

            color = ROUTE_COLORS[i % len(ROUTE_COLORS)]
            map_data["routes"].append({
                "name": r_name,
                "color": color,
                "points": [[la, lo] for la, lo in zip(r_lats, r_lons)],
                "labels": r_labels,
                "connections": r_connections,
                "sizes": r_sizes,
            })
            all_lats.extend(r_lats)
            all_lons.extend(r_lons)
            points_used += len(r_lats)
    else:
        import re as _re
        raw_lats = [float(v) for v in (data.get("lat") or [])]
        raw_lons = [float(v) for v in (data.get("lon") or [])]
        if len(raw_lats) > MAX_MAP_POINTS:
            map_truncated = True
        lats = raw_lats[:MAX_MAP_POINTS]
        lons = raw_lons[:MAX_MAP_POINTS]
        labels = (data.get("labels") or [str(i) for i in range(len(lats))])[:MAX_MAP_POINTS]
        values = data.get("values") or []
        sizes  = data.get("sizes")  or []
        groups_flat = data.get("groups") or []

        # ── Auto-extract groups from label first line when groups not supplied ──
        # Labels are expected to start with the group name (container/track number)
        # followed by <br>.  E.g. "MSDU1234567<br>Houston<br>2026-02-09 (Actual)"
        # Extract the first <br>-delimited segment as the group key.
        if not groups_flat and labels:
            # Try to extract container/tracking number from first <br> segment.
            # A valid group key looks like a container number: 4 uppercase letters
            # followed by 7 digits (e.g. GAOU6335790) or a tracking number.
            _id_pat = _re.compile(r'^[A-Z]{2,6}[0-9]{4,12}$')
            extracted = []
            for lbl in labels:
                norm = lbl.replace("\\n", "<br>").replace("\n", "<br>")
                first = _re.split(r"<br\s*/?>", norm, maxsplit=1)[0].strip()
                # Strip any HTML tags
                first = _re.sub(r"<[^>]+>", "", first).strip()
                extracted.append(first if first else "")
            # Use extracted groups only when all non-empty values look like IDs
            # (uppercase+digits) and there are multiple distinct values
            valid = [g for g in extracted if g]
            distinct = set(valid)
            if len(distinct) > 1 and all(_id_pat.match(g) for g in valid):
                groups_flat = extracted

        if groups_flat and len(groups_flat) == len(lats):
            # Build one route per unique group, preserving insertion order
            import collections as _col
            seen_grp: dict = _col.OrderedDict()
            for idx, grp in enumerate(groups_flat):
                grp = str(grp)
                if grp not in seen_grp:
                    seen_grp[grp] = {"lat": [], "lon": [], "labels": [], "sizes": []}
                if idx < len(lats):
                    seen_grp[grp]["lat"].append(lats[idx])
                    seen_grp[grp]["lon"].append(lons[idx])
                    seen_grp[grp]["labels"].append(labels[idx] if idx < len(labels) else "")
                    seen_grp[grp]["sizes"].append(sizes[idx] if idx < len(sizes) else None)

            for gi, (grp_name, gdata) in enumerate(seen_grp.items()):
                g_lats, g_lons, g_lbls, g_sizes = _dedup_route(
                    gdata["lat"], gdata["lon"], gdata["labels"],
                    [s for s in gdata["sizes"] if s is not None],
                )
                g_conn = [[j, j + 1] for j in range(len(g_lats) - 1)] if len(g_lats) > 1 else []
                color = ROUTE_COLORS[gi % len(ROUTE_COLORS)]
                map_data["routes"].append({
                    "name": grp_name,
                    "color": color,
                    "points": [[la, lo] for la, lo in zip(g_lats, g_lons)],
                    "labels": g_lbls,
                    "connections": g_conn,
                    "sizes": g_sizes,
                })
                all_lats.extend(g_lats)
                all_lons.extend(g_lons)
        else:
            # Single route — deduplicate consecutive identical coordinates
            dedup_lats: list[float] = []
            dedup_lons: list[float] = []
            dedup_labels: list[str] = []
            dedup_sizes: list = []
            for i, (la, lo) in enumerate(zip(lats, lons)):
                lbl = labels[i] if i < len(labels) else str(i)
                sz  = sizes[i]  if i < len(sizes)  else None
                if dedup_lats and abs(la - dedup_lats[-1]) < 1e-6 and abs(lo - dedup_lons[-1]) < 1e-6:
                    if len(lbl) > len(dedup_labels[-1]):
                        dedup_labels[-1] = lbl
                else:
                    dedup_lats.append(la); dedup_lons.append(lo)
                    dedup_labels.append(lbl); dedup_sizes.append(sz)

            lats, lons, labels = dedup_lats, dedup_lons, dedup_labels
            sizes = [s for s in dedup_sizes if s is not None]
            connections = data.get("connections") or []
            if arrows and not connections and len(lats) > 1:
                connections = [[i, i + 1] for i in range(len(lats) - 1)]
            if values and len(values) >= len(lats):
                labels = [f"{lb}<br>{v}" for lb, v in zip(labels, values)]
            map_data["routes"].append({
                "name": "",
                "color": ROUTE_COLORS[0],
                "points": [[la, lo] for la, lo in zip(lats, lons)],
                "labels": labels,
                "connections": connections,
                "sizes": sizes,
            })
            all_lats.extend(lats)
            all_lons.extend(lons)

    # ── Centre / zoom ────────────────────────────────────────────────────
    if all_lats:
        center_lat = sum(all_lats) / len(all_lats)
        center_lon = sum(all_lons) / len(all_lons)
        spread = max(
            max(all_lats) - min(all_lats),
            max(all_lons) - min(all_lons),
        )
        zoom = 12 if spread < 0.5 else 9 if spread < 2 else 6 if spread < 10 else 4 if spread < 40 else 3 if spread < 100 else 2
    else:
        center_lat, center_lon, zoom = 20.0, 0.0, 2

    data_json = _json.dumps(map_data)

    # ── HTML template (uses ||| placeholders to avoid f-string conflicts) ─
    # Embed Leaflet CSS + JS inline so the map works without any CDN/internet.
    _vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    try:
        with open(os.path.join(_vendor_dir, "leaflet.css"), encoding="utf-8") as _f:
            _leaflet_css = _f.read()
        with open(os.path.join(_vendor_dir, "leaflet.js"), encoding="utf-8") as _f:
            _leaflet_js = _f.read()
        with open(os.path.join(_vendor_dir, "topojson-client.min.js"), encoding="utf-8") as _f:
            _topojson_js = _f.read()
        _inline_head = (
            f"<style>\n{_leaflet_css}\n</style>\n"
            f"<script>\n{_leaflet_js}\n</script>\n"
            f"<script>\n{_topojson_js}\n</script>"
        )
        _cdn_tags = (
            '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>\n'
            '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>\n'
            '<script src="https://cdn.jsdelivr.net/npm/topojson-client@3/dist/topojson-client.min.js"></script>'
        )
    except Exception:
        _inline_head = None

    html = _LEAFLET_MAP_HTML
    if _inline_head:
        html = html.replace(_cdn_tags, _inline_head)
    html = html.replace("|||TITLE|||", title)
    html = html.replace("|||DATA_JSON|||", data_json)
    html = html.replace("|||CENTER_LAT|||", str(round(center_lat, 6)))
    html = html.replace("|||CENTER_LON|||", str(round(center_lon, 6)))
    html = html.replace("|||ZOOM|||", str(zoom))

    filename = f"{file_id}_{title_slug}.html"
    full_path = os.path.join(file_store_path, filename)
    with open(full_path, "w", encoding="utf-8") as fh:
        fh.write(html)

    meta = _file_meta(file_id, f"{title_slug}.html", "text/html", full_path)
    meta["map_truncated"] = map_truncated
    return meta


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

    elif plot_type == "bar_stacked":
        PALETTE = [
            "#1F4788", "#E07B39", "#2ECC71", "#E74C3C", "#9B59B6",
            "#F39C12", "#1ABC9C", "#E91E63", "#00BCD4", "#8BC34A",
        ]
        series_list = data.get("series", [])
        traces = []
        for idx, s in enumerate(series_list):
            traces.append(go.Bar(
                name=s.get("name", f"Series {idx + 1}"),
                x=labels,
                y=s.get("values", []),
                marker_color=PALETTE[idx % len(PALETTE)],
            ))
        fig = go.Figure(data=traces)
        fig.update_layout(barmode="stack")

    elif plot_type == "map":
        return _plot_leaflet_map(
            title, data, file_id, title_slug, file_store_path,
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
