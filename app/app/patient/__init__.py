from .routes import register_patient_routes
from .api_routes import register_patient_api_routes


def init_app(app):
    register_patient_routes(app)
    register_patient_api_routes(app)