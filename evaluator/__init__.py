# Expose the Celery app so the worker can import it using: from evaluator import celery_app
from .celery import app as celery_app

__all__ = ("celery_app",)
