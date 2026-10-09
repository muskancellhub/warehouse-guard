#!/usr/bin/env python3
"""StreetCast app server.

Serves the single-page UI, ride and briefing JSON, and a video relay to the
team's VSS backend. Standard library only, so the same file runs on a Mac, on
the workshop VM, and in the python:3.12-slim pod used by deploy-app-no-registry.

Data contract (all files live in this flat folder; the ConfigMap skips subfolders):
  ride_<video>.json                            saved RideReport output
  brief_<destination>_<time_of_day>_<mode>.json  saved Block briefing output
  eval_results.json                            hand-checked claims for the Eval tab
  mock_ride.json, mock_brief.json, mock_eval.json  used only when nothing is saved
  api.py (optional)                            list_rides(), run_ride(video),
                                               run_brief(destination, time_of_day, mode)
Every event and script line carries "source" (an s3:// URI) for /video.
"""
import glob
import json
import os
import re
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8080"))
UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/skyline.svg": ("skyline.svg", "image/svg+xml"),
}

try:
    import api  # Engineer A's module; optional, the saved JSON is the fallback
except Exception as exc:  # missing packages on the cluster must not stop the app
    api = None
    print(f"api.py not loaded ({exc}); serving saved JSON only", file=sys.stderr)

SSL_CTX = ssl.create_default_context()
if os.environ.get("VSS_INSECURE") == "1":
    SSL_CTX.check_hostname = False
    SSL_CTX.verify_mode = ssl.CERT_NONE


# ---------------------------------------------------------------- VSS relay

def _team_config():
    """Values from the VM's /config/<team>.config, read without sourcing it."""
    values = {}
    paths = sorted(glob.glob("/config/*.config"))
    if not paths:
        return values
    with open(paths[0]) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def vss_settings():
    url = os.environ.get("VSS_URL")
    user = os.environ.get("VSS_USERNAME")
    password = os.environ.get("VSS_PASSWORD")
    if not (url and user and password):
        cfg = _team_config()
        url = url or cfg.get("INGRESS_URL")
        user = user or cfg.get("USERNAME")
        password = password or cfg.get("PASSWORD")
    return (url.rstrip("/") if url else None), user, password


_token = None
_token_lock = threading.Lock()


def vss_token(force=False):
    global _token
    with _token_lock:
        if _token and not force:
            return _token
        url, user, password = vss_settings()
        if not (url and user and password):
            raise RuntimeError("VSS credentials are not configured on this machine")
        body = json.dumps({"username": user, "password": password}).encode()
        req = urllib.request.Request(
            url + "/api/v1/auth/login", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
            _token = json.load(resp)["access_token"]
        return _token


def open_stream(source, range_header, token):
    url, _, _ = vss_settings()
    query = urllib.parse.urlencode({"source": source, "token": token})
    headers = {"Range": range_header} if range_header else {}
    req = urllib.request.Request(f"{url}/api/v1/videos/stream?{query}", headers=headers)
    return urllib.request.urlopen(req, timeout=30, context=SSL_CTX)


# ---------------------------------------------------------------- data

def load(name):
    path = os.path.join(HERE, name)
    if not os.path.isfile(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def slug(value, lower=False):
    value = (value or "").strip().replace(" ", "_")
    return UNSAFE.sub("", value.lower() if lower else value)


def saved(pattern):
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, pattern)))


def strip_tokens(value):
    """Drop stream_url fields everywhere: they embed a VSS login token."""
    if isinstance(value, dict):
        return {k: strip_tokens(v) for k, v in value.items() if k != "stream_url"}
    if isinstance(value, list):
        return [strip_tokens(v) for v in value]
    return value


def fill_sources(items, refs):
    """Give each event or script line the source and camera of the clip it names."""
    lookup = {r.get("clip"): r for r in refs or [] if isinstance(r, dict) and r.get("clip")}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        ref = lookup.get(item.get("clip") or "") or {}
        if not item.get("source") and ref.get("source"):
            item["source"] = ref["source"]
        camera = item.get("camera_id") or ref.get("camera_id")
        if camera and not item.get("camera"):
            item["camera"] = camera


def normalize_ride(data):
    """Accepts the engine's raw /ride response as well as the app's own format."""
    data = strip_tokens(data)
    fill_sources(data.get("segments"), [])
    fill_sources(data.get("events"), data.get("segments"))
    return data


def normalize_brief(data):
    """Accepts the engine's raw /brief response as well as the app's own format."""
    data = strip_tokens(data)
    fill_sources(data.get("script"), data.get("clips"))
    return data


def list_rides():
    rides = []
    for name in saved("ride_*.json"):
        data = load(name) or {}
        key = name[len("ride_"):-len(".json")]
        rides.append({"video": key, "label": data.get("label") or data.get("video") or key,
                      "events": len(data.get("events") or [])})
    if rides:
        return {"rides": rides, "mock": False}
    if api:
        try:
            live = api.list_rides() or []
            norm = [r if isinstance(r, dict) else {"video": str(r)} for r in live]
            for r in norm:
                r.setdefault("label", r.get("video"))
            return {"rides": norm, "mock": False}
        except Exception as exc:
            print(f"api.list_rides failed: {exc}", file=sys.stderr)
    mock = load("mock_ride.json") or {}
    return {"rides": [{"video": "mock", "label": f"{mock.get('video', 'Sample ride')} (sample)",
                       "events": len(mock.get("events") or [])}], "mock": True}


def get_ride(video):
    key = slug(video)
    if key and key != "mock":
        data = load(f"ride_{key}.json")
        if data is None and api:
            try:
                data = api.run_ride(video)
            except Exception as exc:
                print(f"api.run_ride failed: {exc}", file=sys.stderr)
        if data is not None:
            data = normalize_ride(data)
            data["mock"] = False
            return data
    data = load("mock_ride.json") or {"events": []}
    data["mock"] = True
    return data


def list_briefs():
    combos = []
    for name in saved("brief_*.json"):
        data = load(name) or {}
        req = data.get("request") or {}
        combos.append({"file": name,
                       "destination": req.get("destination"),
                       "time_of_day": req.get("time_of_day"),
                       "mode": req.get("mode")})
    return combos


def get_brief(destination, time_of_day, mode):
    name = f"brief_{slug(destination, True)}_{slug(time_of_day, True)}_{slug(mode, True)}.json"
    data = load(name)
    if data is None and api:
        try:
            data = api.run_brief(destination, time_of_day, mode)
        except Exception as exc:
            print(f"api.run_brief failed: {exc}", file=sys.stderr)
    if data is not None:
        data = normalize_brief(data)
        data["mock"] = False
        return data
    if saved("brief_*.json"):
        # Real briefings exist, just not this one: say so instead of showing the sample.
        return {"script": [], "mock": False, "available": list_briefs(),
                "message": "No briefing saved for this combination yet."}
    data = load("mock_brief.json") or {"script": []}
    data["mock"] = True
    return data


def get_eval():
    data = load("eval_results.json")
    if data is not None:
        data["mock"] = False
        return data
    data = load("mock_eval.json") or {"rows": []}
    data["mock"] = True
    return data


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "StreetCast/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}\n")

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path
        if path == "/app" or path.startswith("/app/"):  # tolerate an unstripped ingress prefix
            path = path[4:] or "/"
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}

        if path == "/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path in STATIC:
            self.send_static(*STATIC[path])
        elif path == "/rides":
            self.send_json(list_rides())
        elif path == "/ride":
            self.send_json(get_ride(params.get("video", "")))
        elif path == "/briefs":
            self.send_json({"briefs": list_briefs()})
        elif path == "/brief":
            self.send_json(get_brief(params.get("destination", ""),
                                     params.get("time_of_day", ""), params.get("mode", "")))
        elif path == "/eval":
            self.send_json(get_eval())
        elif path == "/video":
            self.relay_video(params.get("source", ""))
        else:
            self.send_json({"error": "not found"}, 404)

    def send_static(self, name, content_type):
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            return self.send_json({"error": f"{name} missing"}, 404)
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def relay_video(self, source):
        if not source.startswith("s3://"):
            return self.send_json({"error": "source must be an s3:// URI"}, 400)
        range_header = self.headers.get("Range")
        try:
            try:
                upstream = open_stream(source, range_header, vss_token())
            except urllib.error.HTTPError as err:
                if err.code != 401:
                    raise
                upstream = open_stream(source, range_header, vss_token(force=True))
        except RuntimeError as err:
            return self.send_json({"error": str(err)}, 503)
        except urllib.error.HTTPError as err:
            return self.send_json({"error": f"VSS returned {err.code}"}, err.code)
        except Exception as err:
            return self.send_json({"error": f"VSS unreachable: {err}"}, 502)

        with upstream:
            self.send_response(upstream.status)
            for header in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                value = upstream.headers.get(header)
                if value:
                    self.send_header(header, value)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    chunk = upstream.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser seeks by dropping the connection


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    relay = "configured" if vss_settings()[0] else "not configured (videos show placeholders)"
    print(f"StreetCast on http://0.0.0.0:{PORT} · api.py {'loaded' if api else 'not loaded'} · VSS relay {relay}",
          flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
