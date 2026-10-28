"""Gunicorn entry point: ``gunicorn --chdir service wsgi:app`` (see Dockerfile)."""

from app import app  # noqa: F401  (imported for its side effect: building app)
