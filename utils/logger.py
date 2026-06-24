"""Logs structurés JSON pour monitoring (journald / Kamatera)."""
import logging, sys
from pythonjsonlogger import jsonlogger
_configured = False
def _configure_root():
    global _configured
    if _configured: return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime":"ts","levelname":"level","name":"agent"}))
    root = logging.getLogger(); root.handlers.clear(); root.addHandler(h)
    root.setLevel(logging.INFO); _configured = True
def get_logger(name):
    _configure_root(); return logging.getLogger(name)
