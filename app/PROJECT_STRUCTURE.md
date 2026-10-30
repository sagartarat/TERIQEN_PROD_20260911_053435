# TERIQEN Project Structure

TERIQEN is a modular Flask application. Business capabilities are grouped under `app/`, while production deployment assets, runtime data, and application entry points live at the repository root.

```text
Cadupius_Healthassist_TERIQEN_Production_Ready_V1.0/
|-- app/                              # Main Flask application package
|   |-- __init__.py                   # Application factory and blueprint registration
|   |-- config.py                     # Environment-based application configuration
|   |-- extensions.py                 # Shared Flask extension initialization
|   |
|   |-- alert_management/             # Alert rules, scheduling, delivery, and dashboard
|   |-- auth/                         # Authentication, forms, throttling, and password reset
|   |-- common/                       # Database, security, logging, access control, and utilities
|   |-- dashboard/                    # Dashboard routes and services
|   |-- file_upload/                  # Patient and workflow upload processing
|   |-- finance/                      # Invoices, reconciliation, printing, and agent payments
|   |-- lead/                         # Lead management routes, APIs, and helpers
|   |-- masters/                      # Master-data services
|   |-- partner/                      # Partner management routes and helpers
|   |-- patient/                      # Patient management routes, APIs, and helpers
|   |-- queue_management/             # Operational queues, parsing, rules, and scheduling
|   |-- reports/                      # Operational reporting routes and services
|   |-- system_control/               # Administrative configuration modules
|   |   |-- email_templates/          # Email-template configuration
|   |   |-- hospital_department_mapping/
|   |   |-- invoice_template_builder/
|   |   |-- round_robin_assignment/
|   |   |-- sla_rule_builder/
|   |   `-- user_management/          # Users and screen-access management
|   |-- system_monitoring/            # Health, scheduler, process, queue, and error monitoring
|   |-- whatsapp/                     # WhatsApp webhook, consent, media, and messaging services
|   |-- workflow/                     # Workflow routes, APIs, and helpers
|   |
|   |-- templates/                    # Jinja2 HTML templates
|   |   `-- system_monitoring/        # Monitoring-specific templates and partials
|   `-- static/                       # Browser-facing assets
|       |-- css/                      # Shared styles
|       |   `-- pages/                # Page-specific styles
|       |-- js/                       # Shared and module scripts
|       |   `-- pages/                # Page-specific scripts
|       |-- images/                   # Image and branding assets
|       `-- downloads/                # Public downloadable templates
|
|-- deploy/                           # Production deployment resources
|   |-- nginx-teriqen.conf         # Nginx reverse-proxy configuration
|   |-- gunicorn.conf.py              # Gunicorn configuration
|   |-- teriqen.service            # Web application systemd unit
|   |-- teriqen-scheduler.service  # Scheduler systemd unit
|   |-- install_server_packages.sh    # Server package installation helper
|   |-- create_mysql_user.sql         # MySQL user setup
|   `-- dpdp_security_migration_v1_1.sql
|                                      # DPDP/security database migration
|
|-- instance/                         # Runtime instance data (not source code)
|   `-- uploads/                      # User-uploaded documents
|       `-- whatsapp_docs/            # WhatsApp document storage by date
|-- logs/                             # Application and module log output
|-- .venv/                            # Local Python virtual environment
|
|-- run.py                            # Local development entry point
|-- wsgi.py                           # Production WSGI entry point
|-- scheduler_worker.py               # Background scheduler entry point
|-- requirements.txt                  # Python dependencies
|-- .env.example                      # Local environment-variable template
|-- .env.production.example           # Production environment template
|-- .env                              # Local secrets; must not be committed
|-- README.md                         # Project overview
|-- PRODUCTION_DEPLOYMENT.md          # Production deployment guide
|-- DPDP_SECURITY_AUDIT_REMEDIATION_V1.1.md
|                                      # Security remediation documentation
`-- TERIQEN_Database_Tables_Columns.xlsx
                                       # Database schema reference workbook
```

## Module Convention

Most feature packages follow a consistent internal pattern:

```text
feature_module/
|-- __init__.py       # Blueprint/package initialization
|-- routes.py         # Server-rendered HTTP routes
|-- api_routes.py     # JSON API endpoints, when required
|-- helpers.py        # Feature-specific helper functions
`-- *_service.py      # Business logic and data operations
```

## Application Layers

| Layer | Location | Responsibility |
|---|---|---|
| Entry points | `run.py`, `wsgi.py`, `scheduler_worker.py` | Start the web application or background scheduler |
| Application setup | `app/__init__.py`, `app/config.py`, `app/extensions.py` | Construct and configure Flask |
| Business modules | `app/<module>/` | Routes, APIs, business rules, and services |
| Shared infrastructure | `app/common/` | Database access, security, logging, validation, and utilities |
| Presentation | `app/templates/`, `app/static/` | Jinja pages, JavaScript, CSS, images, and downloads |
| Operations | `deploy/`, `logs/`, `instance/` | Deployment configuration and runtime-generated data |

## Source-Control Boundaries

The following contain local, generated, sensitive, or runtime data and should remain outside source control:

- `.env`
- `.venv/`
- `logs/`
- `instance/uploads/`
- `__pycache__/` and `*.pyc`

Only the corresponding example environment files should be committed.
