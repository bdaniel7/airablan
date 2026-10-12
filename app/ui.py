import json

from aiohttp import web

HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stations</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; max-width: 760px; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.4rem; }
  form { display: grid; gap: .5rem; padding: 1rem; border: 1px solid #888; border-radius: 8px; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: .5rem; }
  label { display: grid; gap: .2rem; font-size: .85rem; }
  input, select, button { padding: .4rem; font-size: 1rem; }
  .folder { margin: 1rem 0; }
  .folder h2 { font-size: 1rem; border-bottom: 1px solid #888; padding-bottom: .25rem; }
  table { width: 100%; border-collapse: collapse; }
  td, th { text-align: left; padding: .3rem .4rem; border-bottom: 1px solid #444; vertical-align: top; }
  .del { color: #c00; background: none; border: none; cursor: pointer; font-size: .9rem; }
  .err { color: #c00; white-space: pre-wrap; }
  .ok { color: #080; }
  .hint { font-size: .85rem; color: #666; }
</style>
</head>
<body>
<h1>Radio stations</h1>
<p class="hint">Served in offline mode on the receiver. Save ONCE per station — it persists to stations.yaml.</p>
<form id="add">
  <div class="row">
    <label>Name
      <input name="name" required placeholder="e.g. Radio ZZZ">
    </label>
    <label>Folder
      <input name="folder" list="folders" required value="My Stations">
    </label>
  </div>
  <datalist id="folders"></datalist>
  <label>Stream URL
    <input name="url" required inputmode="url" placeholder="http://stream.example.com/live.mp3">
  </label>
  <div class="row">
    <label>Codec
      <select name="codec">
        <option>MP3</option>
        <option>AAC</option>
        <option>WMA</option>
        <option>Vorbis</option>
      </select>
    </label>
    <label>Bitrate (kb/s)
      <input name="bitrate" type="number" min="8" max="512" step="8" value="128">
    </label>
  </div>
  <div class="row">
    <button type="submit">Add station</button>
    <button type="button" id="newFolder">New folder only</button>
  </div>
</form>
<div id="status" class="hint"></div>
<div id="list"></div>
<script>
const listEl = document.getElementById('list');
const statusEl = document.getElementById('status');
const folderInput = document.querySelector('input[name=folder]');
const foldersEl = document.getElementById('folders');

async function api(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || r.status + ' ' + r.statusText);
  return body;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function load() {
  const data = await api('/api/stations');
  const folders = [...new Set(data.stations.map(s => s.folder))];
  foldersEl.innerHTML = folders.map(f => `<option value="${esc(f)}">`).join('');
  listEl.innerHTML = folders.map(folder => {
    const rows = data.stations.filter(s => s.folder === folder)
      .map(s => `<tr><td><button class="del" data-rid="${esc(s.id)}" title="Remove">&#10005;</button></td>
        <td>${esc(s.name)}</td><td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a></td>
        <td>${esc(s.codec)} ${esc(s.bitrate)}</td></tr>`).join('');
    const count = data.stations.filter(s => s.folder === folder).length;
    return `<div class="folder"><h2>${esc(folder)} (${count})</h2>
      <table><tbody>${rows}</tbody></table></div>`;
  }).join('');
}

document.getElementById('add').addEventListener('submit', async function (e) {
  e.preventDefault();
  statusEl.className = 'hint';
  const fd = new FormData(this);
  try {
    const added = await api('/api/stations', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(Object.fromEntries(fd.entries()))
    });
    statusEl.className = 'ok';
    statusEl.textContent = 'Added ' + added.name;
    this.reset();
    if (added.url && added.url.startsWith('https://')) {
      statusEl.textContent += ' (note: the R-N602 cannot play https streams)';
    }
    window.addEventListener('DOMContentLoaded', load);
    await load();
  } catch (err) {
    statusEl.className = 'err';
    statusEl.textContent = err.message;
  }
});

document.getElementById('newFolder').addEventListener('click', function () {
  folderInput.value = 'New folder';
  folderInput.focus();
  folderInput.select();
});

listEl.addEventListener('click', async function (e) {
  const btn = e.target.closest('.del');
  if (!btn) return;
  if (!confirm('Remove this station?')) return;
  try {
    await api('/api/stations/' + encodeURIComponent(btn.dataset.rid), {method: 'DELETE'});
    await load();
  } catch (err) {
    statusEl.className = 'err';
    statusEl.textContent = err.message;
  }
});

load().catch(err => { statusEl.className = 'err'; statusEl.textContent = err.message; });
</script>
</body>
</html>
"""


class UiApp:
    def __init__(self, offline):
        self.offline = offline
        app = web.Application()
        app.router.add_get("/", self.index)
        app.router.add_get("/api/stations", self.list_stations)
        app.router.add_post("/api/stations", self.add_station)
        app.router.add_delete("/api/stations/{rid}", self.delete_station)
        self.app = app

    async def index(self, request):
        return web.Response(text=HTML, content_type="text/html")

    async def list_stations(self, request):
        stations = []
        for folder, items in self.offline.folders:
            for s in items:
                stations.append(
                    {
                        "id": s["id"],
                        "name": s["name"],
                        "url": s["url"],
                        "codec": s["codec"],
                        "bitrate": s["bitrate"],
                        "folder": folder,
                        "slogan": s["slogan"],
                    }
                )
        return web.json_response({"stations": stations})

    async def add_station(self, request):
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)
        spec = {
            "url": data.get("url", "").strip(),
            "codec": (data.get("codec") or "MP3").strip() or "MP3",
            "bitrate": int(data.get("bitrate") or 128),
        }
        try:
            station = self.offline.add_station(
                data.get("folder"), data.get("name"), spec
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(
            {"id": station["id"], "name": station["name"], "url": station["url"], "codec": station["codec"], "bitrate": station["bitrate"]}
        )

    async def delete_station(self, request):
        rid = request.match_info["rid"]
        if not self.offline.remove_station(rid):
            return web.json_response({"error": "station not found"}, status=404)
        return web.json_response({"ok": True})