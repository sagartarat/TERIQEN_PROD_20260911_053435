from flask import Blueprint


alert_management_bp = Blueprint(
    "alert_management",
    __name__,
    url_prefix="/alert-management",
)


def init_app(app):
    """
    Register Alert Management blueprint with the Flask application.
    """

    # Import routes so Flask discovers the route definitions.
    from app.alert_management import routes  # noqa: F401

    app.register_blueprint(alert_management_bp)