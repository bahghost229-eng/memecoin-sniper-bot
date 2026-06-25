"""Chargement de config + overlay runtime non destructif.

config.yaml (annoté, jamais réécrit) est la base. Les changements faits à chaud via la
commande Telegram /config sont écrits dans config.runtime.yaml et fusionnés par-dessus
au démarrage -> ils survivent au redémarrage sans détruire les commentaires de la base.
"""
import os
import yaml

DEFAULT_CONFIG = "config.yaml"
DEFAULT_OVERRIDES = "config.runtime.yaml"


def deep_merge(base: dict, over: dict) -> dict:
    """Fusionne over dans base (récursif sur les dicts). Mute et renvoie base."""
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path: str = DEFAULT_CONFIG, overrides_path: str = DEFAULT_OVERRIDES) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if overrides_path and os.path.exists(overrides_path):
        with open(overrides_path, "r", encoding="utf-8") as f:
            deep_merge(cfg, yaml.safe_load(f) or {})
    return cfg


def save_override(section: str, key: str, value, overrides_path: str = DEFAULT_OVERRIDES) -> None:
    """Écrit/maj une clé dans l'overlay runtime (sans toucher à config.yaml)."""
    data = {}
    if os.path.exists(overrides_path):
        with open(overrides_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    data.setdefault(section, {})[key] = value
    tmp = overrides_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    os.replace(tmp, overrides_path)   # écriture atomique
