"""
Route blueprints for the Sealine Data Chat API.

Import all blueprints here so the application factory can register them in
one step::

    from server.routes import all_blueprints
    for bp in all_blueprints:
        app.register_blueprint(bp)
"""

from server.routes.files import files_bp
from server.routes.health import health_bp
from server.routes.messages import messages_bp
from server.routes.sessions import sessions_bp
from server.routes.teams import teams_bp

all_blueprints = [
    health_bp,
    sessions_bp,
    messages_bp,
    files_bp,
    teams_bp,
]

__all__ = [
    "all_blueprints",
    "health_bp",
    "sessions_bp",
    "messages_bp",
    "files_bp",
    "teams_bp",
]
