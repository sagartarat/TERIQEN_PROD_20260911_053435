from app.system_control.email_templates.routes import register_email_template_routes


def init_app(app):
    register_email_template_routes(app)