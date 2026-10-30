from app.system_control.routes import register_system_control_routes
from app.system_control.email_templates import init_app as email_templates_init_app


def init_app(app):
    register_system_control_routes(app)
    email_templates_init_app(app)