from .routes import register_auth_routes


def init_app(app):
    register_auth_routes(app)