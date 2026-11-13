"""Shared transport settings; no credentials are returned to the browser."""
import os


def request_timeout():
    try:
        value = int(os.environ.get('QWEN_TIMEOUT_SECONDS', '600'))
        return min(600, max(30, value))
    except ValueError:
        return 600
