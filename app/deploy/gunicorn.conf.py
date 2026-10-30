import multiprocessing

bind = "unix:/run/teriqen/teriqen.sock"
workers = 2
worker_class = "gthread"
threads = 4
timeout = 120
graceful_timeout = 30
keepalive = 5
max_requests = 2000
max_requests_jitter = 200
accesslog = "-"
errorlog = "-"
capture_output = True

# Gunicorn runtime control socket
control_socket = "/run/teriqen/gunicorn.ctl"
control_socket_mode = 0o600

# Restrict Unix application socket to owner and www-data group
umask = 0o007
