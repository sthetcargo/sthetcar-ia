"""Gunicorn settings for image-heavy synchronous AI requests.

A request can call Gemini several times for plate detection, visual analysis,
and caption generation. Keep a bounded worker timeout instead of the 30-second
Gunicorn default, while the SDK's per-call timeout limits stalled network reads.
"""
timeout = 180
graceful_timeout = 30
