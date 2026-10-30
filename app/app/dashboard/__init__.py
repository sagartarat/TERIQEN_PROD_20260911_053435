from .routes import register_dashboard_routes


def init_app(app):
    register_dashboard_routes(app)