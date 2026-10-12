#!/usr/bin/env python3
"""Merge stations captured in proxy mode into stations.yaml.

In proxy mode, every station played on the receiver is logged by the server to
data/stations_seen.yaml (one entry per station). This script copies new stations
into stations.yaml under a folder, so they become available in offline mode.

Usage:
    python scripts/adopt_stations.py [--from data/stations_seen.yaml]
                                     [--into stations.yaml]
                                     [--folder "Proxy captured"]
    python scripts/adopt_stations.py --list
Stations that already exist in stations.yaml (same airable id or stream URL)
are skipped.
"""

import argparse
import logging
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser(
        description="merge proxy-captured stations into stations.yaml"
    )
    parser.add_argument("--from", dest="src", default="data/stations_seen.yaml")
    parser.add_argument("--into", dest="dst", default="stations.yaml")
    parser.add_argument("--folder", default="Proxy captured")
    parser.add_argument(
        "--list", action="store_true", help="only list captured stations"
    )
    args = parser.parse_args()

    src = Path(args.src)
    dst = Path(args.dst)
    if not src.exists():
        logging.error("not found: %s", src)
        return 1

    entries = yaml.safe_load(src.read_text(encoding="utf-8")) or []
    seen = [e for e in entries if isinstance(e, dict) and e.get("id") and e.get("url")]

    if args.list:
        for e in sorted(seen, key=lambda e: (e.get("name") or "").lower()):
            print(f"{e.get('id')}\t{e.get('name')}\t{e.get('url')}")
        return 0

    out = yaml.safe_load(dst.read_text(encoding="utf-8")) if dst.exists() else {}
    out = out or {}

    existing_ids = set()
    existing_urls = set()
    for folder, items in out.items():
        for name, spec in items.items():
            if isinstance(spec, str):
                spec = {"url": spec}
            if spec.get("id"):
                existing_ids.add(str(spec["id"]))
            existing_urls.add(spec.get("url"))

    folder = args.folder
    out.setdefault(folder, {})
    added = 0
    for e in sorted(seen, key=lambda e: (e.get("name") or "").lower()):
        rid = str(e["id"])
        if rid in existing_ids or e["url"] in existing_urls:
            continue
        spec = {"id": rid, "url": e["url"]}
        if e.get("codec"):
            spec["codec"] = e["codec"]
        if e.get("bitrate"):
            spec["bitrate"] = e["bitrate"]
        name = (e.get("name") or rid).strip()
        if not name or name == rid:
            name = f"Station {rid}"
        out[folder][name] = spec
        existing_ids.add(rid)
        existing_urls.add(e["url"])
        added += 1
        print(f"added {name} ({e['url']})")

    dst.write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"added {added} station(s) to {dst} under '{folder}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())