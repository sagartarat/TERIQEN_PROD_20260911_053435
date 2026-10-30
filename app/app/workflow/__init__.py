from .routes import register_workflow_routes
from .api_routes import register_workflow_api_routes


def init_app(app):
    register_workflow_routes(app)
    register_workflow_api_routes(app)