from flask import Blueprint

broadcast_management_bp = Blueprint(
    "broadcast_management",
    __name__,
    url_prefix="/broadcast-management",
)

def init_app(app):
    from app.broadcast_management import routes  # noqa: F401
    app.register_blueprint(broadcast_management_bp)


