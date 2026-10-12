import hashlib
import secrets
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import yaml

SERVICE_LISTINGS = {
    "tidal": "TIDAL",
    "napster": "Napster",
    "deezer": "Deezer",
    "qobuz": "Qobuz",
    "amazon": "Amazon Music",
    "palco": "Palco MP3",
    "audioboom": "AudioBoom",
}


class OfflineServer:
    def __init__(self, stations_file):
        self.token = secrets.token_hex(32)
        self.by_id = {}
        self.folders = []
        self.stations_file = stations_file
        self._load(stations_file)

    def _load(self, stations_file):
        path = Path(stations_file)
        if not path.exists():
            return
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for folder, items in data.items():
            stations = []
            for name, spec in items.items():
                if isinstance(spec, str):
                    spec = {"url": spec}
                station = self._make_station(name, spec)
                stations.append(station)
                self.by_id[station["id"]] = station
            self.folders.append((folder, stations))

    @staticmethod
    def _gen_id(seed):
        return str(int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16], 16))

    def _make_station(self, name, spec):
        url = spec["url"]
        rid = str(spec.get("id") or self._gen_id("radio:" + url))
        stream_id = str(spec.get("stream_id") or self._gen_id("stream:" + url))
        return {
            "id": rid,
            "stream": stream_id,
            "name": name,
            "url": url,
            "codec": spec.get("codec", "MP3"),
            "bitrate": int(spec.get("bitrate", 128)),
            "slogan": spec.get("slogan", ""),
        }

    def add_station(self, folder, name, spec):
        folder = (folder or "My Stations").strip() or "My Stations"
        name = (name or "").strip()
        url = (spec.get("url") or "").strip()
        if not name or not url:
            raise ValueError("name and URL are required")
        if not url.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        for i, (f, stations) in enumerate(self.folders):
            if f == folder:
                break
        else:
            self.folders.append((folder, []))
            i = len(self.folders) - 1
        station = self._make_station(name, spec)
        if any(s["id"] == station["id"] for s in self.folders[i][1]):
            raise ValueError("a station with that id already exists")
        self.folders[i][1].append(station)
        self.by_id[station["id"]] = station
        self._persist()
        return station

    def remove_station(self, rid):
        station = self.by_id.pop(str(rid), None)
        if station is None:
            return False
        for folder, stations in self.folders:
            for s in stations:
                if s["id"] == str(rid):
                    stations.remove(s)
                    break
        self.folders = [(f, st) for f, st in self.folders if st]
        self._persist()
        return True

    def _persist(self):
        out = {}
        for folder, stations in self.folders:
            out[folder] = {}
            for s in stations:
                spec = {"url": s["url"]}
                if s["id"] != self._gen_id("radio:" + s["url"]):
                    spec["id"] = s["id"]
                if s["stream"] != self._gen_id("stream:" + s["url"]):
                    spec["stream_id"] = s["stream"]
                if s["codec"] != "MP3":
                    spec["codec"] = s["codec"]
                if s["bitrate"] != 128:
                    spec["bitrate"] = s["bitrate"]
                if s["slogan"]:
                    spec["slogan"] = s["slogan"]
                out[folder][s["name"]] = spec
        with open(self.stations_file, "w", encoding="utf-8") as fh:
            yaml.safe_dump(out, fh, allow_unicode=True, sort_keys=False)

    def route(self, host, method, path):
        parts = urlsplit(path)
        segs = [unquote(s) for s in parts.path.split("/") if s]
        base = f"https://{host}"

        if not segs:
            return 200, self._root(base)
        if segs[0] == "authentication":
            return 200, {
                "id": ["airable", "accesstoken", "authentication"],
                "token": self.token,
                "time": int(time.time()),
            }
        if segs[0] == "actions":
            return 200, {"id": ["airable", "action", "accepted"]}
        if len(segs) == 1 and segs[0] in SERVICE_LISTINGS:
            title = SERVICE_LISTINGS[segs[0]]
            return self._empty_listing(segs[0], title, base, parts.path)
        if segs[0] == "airable" and len(segs) >= 2:
            if segs[1] == "statistics":
                return 200, {"status": "ok"}
            if segs[1] == "radios":
                return self._radios(segs, base)
            if segs[1] == "feeds":
                return self._empty_listing("feeds", "Podcasts", base, parts.path)
            if segs[1] == "play" and len(segs) >= 5 and segs[2] == "radio":
                return self._play(segs[3], segs[4], base)
        if segs[0] == "id" and len(segs) >= 4 and segs[1] == "airable" and segs[2] == "radio":
            return self._record(segs[3], base)
        return 404, {"id": ["airable", "error", "notfound"], "title": "Not found"}

    def _root(self, base):
        services = [
            ("airable.radios", "Radio", "/airable/radios"),
            ("airable.feeds", "Podcasts", "/airable/feeds"),
            ("tidal", "TIDAL", "/tidal"),
            ("napster", "Napster", "/napster"),
            ("deezer", "Deezer", "/deezer"),
            ("qobuz", "Qobuz", "/qobuz"),
            ("audioboom", "AudioBoom", "/audioboom"),
            ("palco", "Palco MP3", "/palco"),
            ("amazon", "Amazon Music", "/amazon"),
        ]
        return {
            "id": ["airable", "listing", "index"],
            "title": "Welcome",
            "url": f"{base}/",
            "content": {
                "entries": [
                    {
                        "id": ["airable", "service", svc],
                        "title": title,
                        "url": f"{base}{href}",
                    }
                    for svc, title, href in services
                ]
            },
        }

    @staticmethod
    def _empty_listing(key, title, base, path):
        return 200, {
            "id": ["airable", "listing", key],
            "title": title,
            "url": f"{base}{path}",
            "content": {"entries": []},
        }

    def _radios(self, segs, base):
        if len(segs) == 2:
            entries = [
                {
                    "id": ["airable", "directory", quote(folder)],
                    "title": folder,
                    "url": f"{base}/airable/radios/{quote(folder)}",
                }
                for folder, _stations in self.folders
            ]
            return 200, {
                "id": ["airable", "listing", "radios"],
                "title": "Radio",
                "url": f"{base}/airable/radios",
                "content": {"entries": entries},
            }
        folder = segs[2] if len(segs) > 2 else None
        for name, stations in self.folders:
            if name == folder:
                entries = [
                    {
                        "id": ["airable", "radio", s["id"]],
                        "title": s["name"],
                        "url": f"{base}/id/airable/radio/{s['id']}",
                        "slogan": s["slogan"],
                    }
                    for s in stations
                ]
                return 200, {
                    "id": ["airable", "listing", quote(folder)],
                    "title": name,
                    "url": f"{base}/airable/radios/{quote(folder)}",
                    "content": {"entries": entries},
                }
        return 404, {"id": ["airable", "error", "notfound"], "title": "Not found"}

    def _record(self, rid, base):
        station = self.by_id.get(rid)
        if station is None:
            return 404, {"id": ["airable", "error", "notfound"], "title": "Not found"}
        return 200, self._station_doc(station, base)

    def _play(self, rid, stream_id, base):
        station = self.by_id.get(rid)
        if station is None:
            return 404, {"id": ["airable", "error", "notfound"], "title": "Not found"}
        return 200, {
            "id": ["airable", "redirect", hashlib.md5(station["url"].encode("utf-8")).hexdigest()],
            "url": station["url"],
            "content": self._station_doc(station, base),
            "events": {
                "start": f"{base}/airable/statistics/{rid}/play/{int(time.time())}",
                "stop": f"{base}/airable/statistics/{rid}/stop/{int(time.time())}",
            },
        }

    def _station_doc(self, station, base):
        rid = station["id"]
        return {
            "id": ["airable", "radio", rid],
            "title": station["name"],
            "url": f"{base}/id/airable/radio/{rid}",
            "actions": [],
            "images": [],
            "slogan": station["slogan"],
            "streams": [
                {
                    "id": ["airable", "radio.stream", station["stream"]],
                    "title": f"{station['codec'].lower()}, {station['bitrate']} kb/s",
                    "url": f"{base}/airable/play/radio/{rid}/{station['stream']}",
                    "codec": {"name": station["codec"], "bitrate": station["bitrate"]},
                    "icy": True,
                }
            ],
        }