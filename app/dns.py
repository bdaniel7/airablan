import asyncio
import logging
import socket
import time
from pathlib import Path

from dnslib import A, DNSRecord, QTYPE, RR

log = logging.getLogger("airable.dns")


class DnsProxy:
    def __init__(self, config, data_dir):
        self.host = config.get("host", "0.0.0.0")
        self.port = int(config.get("port", 53))
        self.upstream_host = config.get("upstream", "1.1.1.1")
        self.upstream_port = int(config.get("upstream_port", 53))
        self.override = config.get("override", {}) or {}
        self.log_path = Path(data_dir) / "dns.log"
        self.transport = None

    async def serve(self):
        loop = asyncio.get_running_loop()

        class _Protocol(asyncio.DatagramProtocol):
            def __init__(self, app):
                self.app = app

            def datagram_received(self, data, addr):
                asyncio.ensure_future(self.app.handle(data, addr))

        transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self), local_addr=(self.host, self.port)
        )
        self.transport = transport
        log.info("dns listening on %s:%s", self.host, self.port)

    async def handle(self, data, addr):
        try:
            req = DNSRecord.parse(data)
            name = str(req.q.qname).rstrip(".")
            qtype = req.q.qtype
            self._log(addr[0], name, qtype)
            if name in self.override:
                if qtype == QTYPE.A:
                    reply = req.reply()
                    reply.add_answer(
                        RR(
                            rname=req.q.qname,
                            rtype=QTYPE.A,
                            rclass=1,
                            ttl=60,
                            rdata=A(self.override[name]),
                        )
                    )
                    payload = reply.pack()
                else:
                    payload = req.reply().pack()
            else:
                loop = asyncio.get_running_loop()
                payload = await loop.run_in_executor(None, self._forward, data)
            if self.transport is not None:
                self.transport.sendto(payload, addr)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("dns handling failed for %s", addr)

    def _forward(self, data):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(5.0)
            sock.sendto(data, (self.upstream_host, self.upstream_port))
            return sock.recvfrom(4096)[0]

    def _log(self, client, name, qtype):
        line = (
            f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
            f"\t{client}\t{name}\t{QTYPE.get(qtype, qtype)}\n"
        )
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(line)