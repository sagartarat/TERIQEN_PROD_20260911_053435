from flask import Blueprint

whatsapp_bp = Blueprint("whatsapp", __name__, url_prefix="/whatsapp")


def init_app(app):
    from app.whatsapp import routes
    app.register_blueprint(whatsapp_bp)