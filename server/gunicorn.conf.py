"""
Gunicorn configuration for Sealine Data Chat API.

Usage:
    gunicorn -c server/gunicorn.conf.py 'server.app:create_app()'
"""

import os

# Bind address and port
bind = "0.0.0.0:{}".format(os.environ.get("PORT", "8080"))

# Number of worker processes — 2 gevent workers sufficient for 1-5 users
workers = int(os.environ.get("WORKERS", "2"))

# Worker class — gevent required for SSE streaming support
worker_class = "gevent"

# Timeout — 5 minutes to allow for complex multi-query agent loops
timeout = 300

# Keep-alive — maintain SSE connections
keepalive = 65

# Access log
accesslog = "-"

# Error log
errorlog = "-"

# Log level
loglevel = "info"
