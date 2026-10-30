from flask import current_app, flash, jsonify, redirect, request


def _prefers_json():
    return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"


def register_error_handlers(app):
    @app.errorhandler(401)
    def unauthorized(e):
        if _prefers_json():
            return jsonify({"success": False, "message": "Authentication required."}), 401
        return "Authentication required.", 401

    @app.errorhandler(403)
    def forbidden(e):
        if _prefers_json():
            return jsonify({"success": False, "message": "You do not have permission to perform this action."}), 403
        return "You do not have permission to perform this action.", 403

    @app.errorhandler(404)
    def not_found(e):
        if _prefers_json():
            return jsonify({"success": False, "message": "Resource not found."}), 404
        return "Resource not found.", 404

    @app.errorhandler(413)
    def too_large(e):
        if _prefers_json():
            return jsonify({"success": False, "message": "Upload exceeds the permitted size limit."}), 413
        flash("Total document upload size cannot exceed the configured limit.", "error")
        return redirect(request.url)

    @app.errorhandler(500)
    def internal_error(e):
        current_app.logger.exception("UNHANDLED_APPLICATION_ERROR")
        if _prefers_json():
            return jsonify({"success": False, "message": "An internal error occurred."}), 500
        return "An internal error occurred.", 500
