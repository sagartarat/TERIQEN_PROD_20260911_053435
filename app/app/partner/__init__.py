from .routes import register_partner_routes


def init_app(app):
    register_partner_routes(app)