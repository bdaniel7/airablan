import hashlib
import json
import time
from pathlib import Path


class ResponseCache:
    def __init__(self, base_dir, ttl_days=365, enabled=True):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_days * 86400
        self.enabled = enabled

    def key(self, host, method, path):
        raw = f"{host}\n{method}\n{path}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def get(self, host, method, path, allow_stale=True):
        if not self.enabled:
            return None
        host_dir = self.base / host
        key = self.key(host, method, path)
        meta_path = host_dir / f"{key}.json"
        body_path = host_dir / f"{key}.bin"
        if not meta_path.exists() or not body_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        age = time.time() - meta["captured_at"]
        if age > self.ttl_seconds and not allow_stale:
            return None
        meta["stale"] = age > self.ttl_seconds
        meta["body"] = body_path.read_bytes()
        return meta

    def put(self, host, method, path, status, headers, body):
        if not self.enabled:
            return
        host_dir = self.base / host
        host_dir.mkdir(parents=True, exist_ok=True)
        key = self.key(host, method, path)
        meta = {
            "host": host,
            "method": method,
            "path": path,
            "status": status,
            "headers": headers,
            "captured_at": time.time(),
            "stale": False,
        }
        (host_dir / f"{key}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        (host_dir / f"{key}.bin").write_bytes(body)