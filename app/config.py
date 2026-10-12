import os

import yaml

DEFAULT_PATH = os.environ.get("AIRABLE_CONFIG", "config.yaml")


def load_config(path=None):
    path = path or DEFAULT_PATH
    with open(path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}
    config.setdefault("data_dir", "data")
    return config