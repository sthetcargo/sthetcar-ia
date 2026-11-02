"""Gunicorn settings for image-heavy AI analysis and live progress polling.

One process with a small gthread pool lets the browser poll the progress endpoint
while a synchronous photo-analysis request is doing CPU/network work. The
bounded pool avoids multiplying the application's image-memory footprint.
"""
worker_class = "gthread"
workers = 1
threads = 4
timeout = 360
graceful_timeout = 30
