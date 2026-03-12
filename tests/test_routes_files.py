"""
Unit tests for the Files route (server/routes/files.py).

Tests:
  - GET /api/files/<file_id> with existing file returns 200
  - GET /api/files/<nonexistent> returns 404
  - Correct Content-Type headers for various file types
  - File on disk missing (expired) returns 410
"""

import os
import json
from datetime import datetime, timezone

import pytest

from server.sessions.store import FileRecord


class TestDownloadFile:
    """GET /api/files/<file_id>"""

    def test_existing_file_returns_200(self, client, created_session, sample_file_record):
        """Downloading an existing file should return 200."""
        fr = sample_file_record(filename="report.xlsx")
        created_session.files.append(fr)

        resp = client.get(f"/api/files/{fr.file_id}")
        assert resp.status_code == 200

    def test_existing_file_returns_content(self, client, created_session, sample_file_record):
        """The response body should contain the file content."""
        fr = sample_file_record(filename="data.csv", content=b"col1,col2\n1,2\n", file_type="text/csv")
        created_session.files.append(fr)

        resp = client.get(f"/api/files/{fr.file_id}")
        assert resp.data == b"col1,col2\n1,2\n"

    def test_nonexistent_file_returns_404(self, client):
        """Requesting a file that does not exist should return 404."""
        resp = client.get("/api/files/nonexistent-file-id")
        assert resp.status_code == 404

    def test_nonexistent_file_error_format(self, client):
        """The 404 response should have the correct error structure."""
        resp = client.get("/api/files/nonexistent-file-id")
        data = resp.get_json()
        assert "error" in data
        assert data["code"] == "FILE_NOT_FOUND"
        assert data["status"] == 404

    def test_file_on_disk_missing_returns_410(self, client, created_session, sample_file_record):
        """If the file record exists but the file on disk has been deleted, return 410."""
        fr = sample_file_record(filename="expired.xlsx")
        created_session.files.append(fr)

        # Remove the file from disk to simulate expiration
        os.remove(fr.file_path)

        resp = client.get(f"/api/files/{fr.file_id}")
        assert resp.status_code == 410


class TestFileContentTypes:
    """Verify correct Content-Type headers for different file types."""

    def test_xlsx_content_type(self, client, created_session, sample_file_record):
        """XLSX files should return the correct MIME type."""
        fr = sample_file_record(
            filename="report.xlsx",
            file_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        created_session.files.append(fr)

        resp = client.get(f"/api/files/{fr.file_id}")
        assert "openxmlformats-officedocument.spreadsheetml.sheet" in resp.content_type

    def test_pdf_content_type(self, client, created_session, sample_file_record):
        """PDF files should return application/pdf."""
        fr = sample_file_record(filename="report.pdf", file_type="application/pdf")
        created_session.files.append(fr)

        resp = client.get(f"/api/files/{fr.file_id}")
        assert "application/pdf" in resp.content_type

    def test_png_content_type(self, client, created_session, sample_file_record):
        """PNG images should return image/png."""
        fr = sample_file_record(filename="chart.png", file_type="image/png")
        created_session.files.append(fr)

        resp = client.get(f"/api/files/{fr.file_id}")
        assert "image/png" in resp.content_type

    def test_csv_content_type(self, client, created_session, sample_file_record):
        """CSV files should return text/csv."""
        fr = sample_file_record(filename="data.csv", file_type="text/csv")
        created_session.files.append(fr)

        resp = client.get(f"/api/files/{fr.file_id}")
        assert "text/csv" in resp.content_type

    def test_html_content_type(self, client, created_session, sample_file_record):
        """HTML files should return text/html."""
        fr = sample_file_record(filename="report.html", file_type="text/html")
        created_session.files.append(fr)

        resp = client.get(f"/api/files/{fr.file_id}")
        assert "text/html" in resp.content_type


class TestFileDisposition:
    """Verify Content-Disposition (attachment vs inline) behavior."""

    def test_png_served_inline(self, client, created_session, sample_file_record):
        """PNG images should be served inline (not as attachment)."""
        fr = sample_file_record(filename="chart.png", file_type="image/png")
        created_session.files.append(fr)

        resp = client.get(f"/api/files/{fr.file_id}")
        # inline means no Content-Disposition: attachment
        cd = resp.headers.get("Content-Disposition", "")
        assert "attachment" not in cd

    def test_xlsx_served_as_attachment(self, client, created_session, sample_file_record):
        """XLSX files should be served as attachment (download)."""
        fr = sample_file_record(filename="report.xlsx", file_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        created_session.files.append(fr)

        resp = client.get(f"/api/files/{fr.file_id}")
        cd = resp.headers.get("Content-Disposition", "")
        assert "attachment" in cd

    def test_attachment_contains_filename(self, client, created_session, sample_file_record):
        """The Content-Disposition header should include the original filename."""
        fr = sample_file_record(filename="my_report.xlsx", file_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        created_session.files.append(fr)

        resp = client.get(f"/api/files/{fr.file_id}")
        cd = resp.headers.get("Content-Disposition", "")
        assert "my_report.xlsx" in cd

    def test_file_across_sessions(self, client, session_store, sample_file_record):
        """Files should be findable across any session, not just the first one."""
        # Create two sessions
        s1 = session_store.create()
        s2 = session_store.create()

        fr = sample_file_record(filename="found_in_s2.txt", file_type="text/plain")
        s2.files.append(fr)

        resp = client.get(f"/api/files/{fr.file_id}")
        assert resp.status_code == 200
