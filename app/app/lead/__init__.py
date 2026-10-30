from .routes import register_lead_routes
from .api_routes import register_lead_api_routes


def init_app(app):
    register_lead_routes(app)
    register_lead_api_routes(app)