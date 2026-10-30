from .routes import register_reports_routes


def init_app(app):
    register_reports_routes(app)