import argparse
import asyncio
import base64
import json
import logging
import os
import re
import socket
import ssl
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import yaml
from aiohttp import web
from aiohttp.abc import AbstractResolver
from aiohttp.resolver import DefaultResolver

from .cache import ResponseCache
from .config import load_config

log = logging.getLogger("airable.proxy")

HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
}

PLAY_RE = re.compile(r"/airable/play/radio/(\d+)/(\d+)")


class _PinnedResolver(AbstractResolver):
    """Resolve specific hostnames to fixed IPs, so the proxy can reach the real
    upstream even when local DNS redirects that hostname to this Pi itself."""

    def __init__(self, mapping):
        self._mapping = dict(mapping)
        self._fallback = DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        ip = self._mapping.get(host)
        if ip:
            return [
                {
                    "hostname": host,
                    "host": ip,
                    "port": port,
                    "family": socket.AF_INET,
                    "proto": socket.IPPROTO_TCP,
                    "flags": 0,
                }
            ]
        return await self._fallback.resolve(host, port, family)

    async def close(self):
        await self._fallback.close()


def _try_text(body):
    if body is None:
        return None
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return None


class ProxyApp:
    def __init__(self, config):
        self.config = config
        self.data_dir = Path(config["data_dir"])
        self.capture_dir = self.data_dir / "captures"
        self.handshake_log = self.data_dir / "handshake.log"
        self.capture_dir.mkdir(parents=True, exist_ok=True)

        cache_cfg = config.get("cache", {}) or {}
        self.cache = ResponseCache(
            self.data_dir / "cache",
            ttl_days=int(cache_cfg.get("ttl_days", 365)),
            enabled=bool(cache_cfg.get("enabled", True)),
        )
        self.stale_on_error = bool(cache_cfg.get("stale_on_error", True))

        stations_log_cfg = config.get("stations_log", {}) or {}
        self.stations_log_enabled = bool(stations_log_cfg.get("enabled", True))
        self.stations_seen_file = self.data_dir / stations_log_cfg.get(
            "file", "stations_seen.yaml"
        )
        self.seen = {}
        if self.stations_seen_file.exists():
            try:
                entries = yaml.safe_load(
                    self.stations_seen_file.read_text(encoding="utf-8")
                ) or []
                self.seen = {
                    str(e["id"]): e for e in entries if isinstance(e, dict) and e.get("id")
                }
            except Exception:
                log.warning("could not read %s", self.stations_seen_file)

        upstream_cfg = config.get("upstream", {}) or {}
        self.verify_ssl = bool(upstream_cfg.get("verify_ssl", True))
        self.timeout = aiohttp.ClientTimeout(
            total=int(upstream_cfg.get("timeout_seconds", 30))
        )

        self.hosts = config["hosts"]
        self.capture_enabled = bool(
            (config.get("capture", {}) or {}).get("enabled", True)
        )
        self.client = None
        self._tls_context = self._build_tls_context()

        self.mode = str(config.get("mode", "proxy")).lower()
        self.mirror_hosts = {
            name for name, info in self.hosts.items() if info.get("mirrored")
        }
        self.upstream_pins = {}
        for info in self.hosts.values():
            ip = info.get("upstream_ip")
            host = urlsplit(info["upstream"]).hostname
            if ip and host:
                self.upstream_pins[host] = ip
        self.offline = None
        if self.mode == "offline":
            from .offline import OfflineServer

            self.offline = OfflineServer(
                self._resolve(config.get("stations_file", "stations.yaml"))
            )

    def _resolve(self, path):
        candidate = Path(path)
        if candidate.is_absolute():
            return str(candidate)
        return str(Path.cwd() / candidate)

    def _build_tls_context(self):
        contexts = {}
        default_cert = None
        for name, info in self.hosts.items():
            cert_path = self._resolve(info["cert"])
            key_path = self._resolve(info["key"])
            if not (Path(cert_path).exists() and Path(key_path).exists()):
                log.warning(
                    "cert missing for %s (%s) - that host is not served over TLS",
                    name,
                    cert_path,
                )
                continue
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
            contexts[name] = ctx
            if default_cert is None:
                default_cert = (cert_path, key_path)

        if default_cert is None:
            raise RuntimeError(
                "no TLS certificates found; run scripts/gen_cert.sh <hostname> first"
            )

        master = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        master.load_cert_chain(certfile=default_cert[0], keyfile=default_cert[1])

        def sni_callback(sslobj, servername, sslctx):
            if servername and servername in contexts:
                if contexts[servername] is not sslobj.context:
                    sslobj.context = contexts[servername]

        master.sni_callback = sni_callback
        return master

    async def handle(self, request):
        host = (request.host or "").split(":", 1)[0].lower()
        method = request.method
        path = request.rel_url.path_qs
        req_headers = dict(request.headers.items())
        req_body = await request.read()

        if self.mode == "offline" and host not in self.mirror_hosts:
            status, payload = self.offline.route(host, method, path)
            log.info("offline %s %s -> %s", method, path, status)
            body = json.dumps(payload, separators=(",", ":"))
            resp_headers = {
                "Expires": "Thu, 19 Nov 1981 08:52:00 GMT",
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Content-Type": "application/json",
            }
            self.write_capture(
                host, method, path, req_headers, req_body, status, resp_headers, body.encode("utf-8")
            )
            return web.Response(
                status=status,
                body=body.encode("utf-8"),
                headers=resp_headers,
            )

        info = self.hosts.get(host)
        if info is None:
            log.warning("unmapped host %s (%s %s)", host, method, path)
            self.write_capture(host, method, path, req_headers, req_body, None, None, None)
            return web.json_response({"error": "unmapped host"}, status=400)

        cached = self.cache.get(host, method, path, allow_stale=True)
        try:
            status, resp_headers, resp_body = await self.forward(
                info, method, path, req_headers, req_body
            )
            self.cache.put(host, method, path, status, resp_headers, resp_body)
        except Exception as exc:
            log.error("upstream %s failed for %s %s: %s", info["upstream"], method, path, exc)
            if cached is not None:
                status, resp_headers, resp_body = (
                    cached["status"],
                    cached["headers"],
                    cached["body"],
                )
                if cached.get("stale"):
                    resp_headers = dict(resp_headers)
                    resp_headers["x-airable-served-from"] = "stale-cache"
            else:
                self.write_capture(host, method, path, req_headers, req_body, None, None, None)
                return web.Response(status=502, text="upstream unavailable")

        self.write_capture(host, method, path, req_headers, req_body, status, resp_headers, resp_body)
        self._log_station(host, path, status, resp_body)
        return web.Response(status=status, body=resp_body, headers=resp_headers)

    def _log_station(self, host, path, status, resp_body):
        if not self.stations_log_enabled or status != 200 or not resp_body:
            return
        match = PLAY_RE.match(path)
        if match is None:
            return
        rid, stream_id = match.group(1), match.group(2)
        try:
            data = json.loads(resp_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        content = data.get("content") or {}
        streams = content.get("streams") or []
        if not streams or not data.get("url"):
            return
        stream = next(
            (
                s
                for s in streams
                if s.get("id") and len(s["id"]) == 3 and s["id"][2] == stream_id
            ),
            streams[0],
        )
        codec = stream.get("codec") or {}
        url = data.get("url") or ""
        if not url.startswith(("http://", "https://")):
            return
        if rid in self.seen:
            return
        self.seen[rid] = {
            "id": rid,
            "name": content.get("title") or f"Station {rid}",
            "url": url,
            "codec": codec.get("name", "MP3"),
            "bitrate": codec.get("bitrate", 128),
        }
        self._save_seen()
        log.info("station logged: %s -> %s", self.seen[rid]["name"], url)

    def _save_seen(self):
        entries = sorted(
            self.seen.values(),
            key=lambda e: (e.get("name") or "").lower(),
        )
        self.stations_seen_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.stations_seen_file.with_suffix(".yaml.tmp")
        tmp.write_text(
            yaml.safe_dump(entries, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(self.stations_seen_file)

    async def forward(self, info, method, path, headers, body):
        url = info["upstream"].rstrip("/") + path
        relay_headers = {
            k: v
            for k, v in headers.items()
            if k.lower() not in HOP_HEADERS and k.lower() != "accept-encoding"
        }
        async with self.client.request(
            method, url, headers=relay_headers, data=body
        ) as resp:
            resp_body = await resp.read()
            resp_headers = {
                k: v for k, v in resp.headers.items() if k.lower() not in HOP_HEADERS
            }
            return resp.status, resp_headers, resp_body

    def write_capture(
        self, host, method, path, req_headers, req_body, status, resp_headers, resp_body
    ):
        if not self.capture_enabled:
            return
        host_dir = self.capture_dir / (host or "unknown")
        host_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", path)[:80] or "root"
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        name = f"{ts}_{uuid.uuid4().hex[:8]}_{method}_{safe}.json"
        record = {
            "host": host,
            "method": method,
            "path": path,
            "request_headers": req_headers,
            "request_body": _try_text(req_body),
            "request_body_base64": (
                base64.b64encode(req_body).decode("ascii") if req_body else None
            ),
            "response_status": status,
            "response_headers": resp_headers,
            "response_body": _try_text(resp_body),
            "response_body_base64": (
                base64.b64encode(resp_body).decode("ascii") if resp_body else None
            ),
        }
        (host_dir / name).write_text(json.dumps(record, indent=2), encoding="utf-8")

    def _exception_handler(self, loop, context):
        exc = context.get("exception")
        message = str(context.get("message", ""))
        when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        is_tls = isinstance(exc, ssl.SSLError) or "ssl" in message.lower() or "handshake" in message.lower()
        if is_tls:
            peer = None
            transport = context.get("transport")
            if transport is not None and hasattr(transport, "get_extra_info"):
                peer = transport.get_extra_info("peername")
            line = f"{when}\t{peer}\t{message}\t{exc!r}\n"
            with open(self.handshake_log, "a", encoding="utf-8") as fh:
                fh.write(line)
            log.warning("TLS problem from %s: %s", peer, exc)
        else:
            loop.default_exception_handler(context)

    async def run(self, run_dns):
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(self._exception_handler)

        connector = aiohttp.TCPConnector(
            ssl=self.verify_ssl,
            resolver=(
                _PinnedResolver(self.upstream_pins) if self.upstream_pins else None
            ),
        )
        self.client = aiohttp.ClientSession(connector=connector, timeout=self.timeout)

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)

        runner = web.AppRunner(app)
        await runner.setup()
        listen = self.config.get("listen", {}) or {}
        host = listen.get("host", "0.0.0.0")
        port = int(listen.get("port", 443))
        site = web.TCPSite(runner, host, port, ssl_context=self._tls_context)
        await site.start()
        log.info("proxy listening on https://%s:%s", host, port)

        http_cfg = self.config.get("listen_http", {}) or {}
        http_site = None
        if http_cfg.get("host"):
            http_host = http_cfg.get("host", "0.0.0.0")
            http_port = int(http_cfg.get("port", 80))
            http_site = web.TCPSite(runner, http_host, http_port)
            await http_site.start()
            log.info("proxy listening on http://%s:%s", http_host, http_port)

        ui_runners = []
        ui_cfg = self.config.get("ui", {}) or {}
        if bool(ui_cfg.get("enabled", False)) and self.mode == "offline" and self.offline is not None:
            from .ui import UiApp

            ui = UiApp(self.offline)
            ui_runner = web.AppRunner(ui.app)
            await ui_runner.setup()
            ui_host = ui_cfg.get("host", "0.0.0.0")
            ui_port = int(ui_cfg.get("port", 9080))
            await web.TCPSite(ui_runner, ui_host, ui_port).start()
            ui_runners.append(ui_runner)
            log.info("station UI listening on http://%s:%s", ui_host, ui_port)

        try:
            self._shutdown = asyncio.Event()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, self._shutdown.set)
                except (NotImplementedError, RuntimeError, ValueError):
                    pass

            if run_dns:
                from .dns import DnsProxy

                dns_cfg = self.config.get("dns", {}) or {}
                dns = DnsProxy(dns_cfg, self.config["data_dir"])
                dns_task = asyncio.create_task(dns.serve())
                await self._shutdown.wait()
                dns_task.cancel()
                try:
                    await dns_task
                except asyncio.CancelledError:
                    pass
            else:
                await self._shutdown.wait()
        finally:
            log.info("shutting down")
            for ui_runner in ui_runners:
                await ui_runner.cleanup()
            await runner.cleanup()
            await self.client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="airable-compatible local API server")
    parser.add_argument(
        "--config", default=os.environ.get("AIRABLE_CONFIG", "config.yaml")
    )
    parser.add_argument(
        "--no-dns", action="store_true", help="disable built-in DNS even if enabled in config"
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    logging.basicConfig(
        level=logging.DEBUG if config.get("debug") else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    app = ProxyApp(config)
    dns_enabled = bool((config.get("dns", {}) or {}).get("enabled", False)) and not args.no_dns
    try:
        asyncio.run(app.run(run_dns=dns_enabled))
    except KeyboardInterrupt:
        log.info("stopped by user")


if __name__ == "__main__":
    main()