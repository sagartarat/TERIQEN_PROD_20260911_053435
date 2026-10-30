from flask import Blueprint


system_monitoring_bp = Blueprint(
    "system_monitoring",
    __name__,
    url_prefix="/system-monitoring",
)


def init_app(app):
    from app.system_monitoring import routes

    app.register_blueprint(system_monitoring_bp)