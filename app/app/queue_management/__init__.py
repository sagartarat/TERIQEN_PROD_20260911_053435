from app.queue_management.routes import register_queue_management_routes


def init_app(app):
    register_queue_management_routes(app)