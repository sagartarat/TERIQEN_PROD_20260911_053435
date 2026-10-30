from .invoice_generation import register_invoice_generation_routes
from .invoice_printing import register_invoice_printing_routes
from .invoice_reconciliation import register_invoice_reconciliation_routes
from .api_routes import register_finance_api_routes


def init_app(app):
    register_invoice_generation_routes(app)
    register_invoice_printing_routes(app)
    register_invoice_reconciliation_routes(app)
    register_finance_api_routes(app)