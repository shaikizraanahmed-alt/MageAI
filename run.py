#!/usr/bin/env python3
"""
run.py — Single entry point for Mage AI
========================================================
Starts TWO servers:
  1.  System-metrics HTTP server  (port 7800)  — serves Frontend/ static files
      and the /api/metrics endpoint (CPU / RAM / GPU usage).
  2.  Main FastAPI / Uvicorn server (port 8000) — serves the full AI backend
      AND mounts the Frontend/ directory at /app/.

Usage:
    python run.py                  # default: host=0.0.0.0, port=8000
    python run.py --host 127.0.0.1
    python run.py --port 9000
    python run.py --no-metrics     # skip the metrics server
    python run.py --reload         # enable uvicorn auto-reload (dev mode)

After starting, open:
    http://localhost:8000           → redirects to /app/ (main UI)
    http://localhost:7800           → standalone metrics/static page
"""

import argparse
import os
import subprocess
import sys
import threading
import time

# ── Make sure the project root is on sys.path ────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── Argument parsing ──────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Mage AI — unified launcher")
parser.add_argument("--host",        default="0.0.0.0",  help="Bind host (default: 0.0.0.0)")
parser.add_argument("--port",        default=8000, type=int, help="Main API port (default: 8000)")
parser.add_argument("--metrics-port",default=7800, type=int, help="Metrics server port (default: 7800)")
parser.add_argument("--no-metrics",  action="store_true",    help="Skip the metrics server")
parser.add_argument("--reload",      action="store_true",    help="Enable uvicorn auto-reload (dev)")
parser.add_argument("--workers",     default=1, type=int,    help="Uvicorn worker count (default: 1)")
args = parser.parse_args()

# ── Auto-install psutil if missing (needed by metrics server) ─────────────────
try:
    import psutil  # noqa: F401
except ImportError:
    print("[INFO] psutil not found - installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "psutil", "-q"])
    print("[INFO] psutil installed")

# ── Metrics server (imported from Frontend/server.py) ────────────────────────
def _start_metrics_server(port: int):
    """Run the metrics HTTP server in this thread (blocking)."""
    import json
    import subprocess as _sp
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
    from urllib.parse import urlparse

    import psutil

    def _gpu_usage() -> float:
        # NVIDIA via nvidia-smi
        try:
            r = _sp.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0 and r.stdout.strip():
                vals = [float(v) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return round(sum(vals) / len(vals), 1)
        except Exception:
            pass

        # Windows Performance Counter (AMD / Intel / NVIDIA)
        try:
            ps_cmd = (
                "Get-Counter '\\GPU Engine(*engtype_3D)\\Utilization Percentage'"
                " -ErrorAction SilentlyContinue"
                " | Select-Object -ExpandProperty CounterSamples"
                " | Measure-Object -Property CookedValue -Sum"
                " | Select-Object -ExpandProperty Sum"
            )
            r = _sp.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return round(min(float(r.stdout.strip()), 100.0), 1)
        except Exception:
            pass

        return -1.0  # GPU not detectable

    frontend_dir = os.path.join(ROOT, "Frontend")

    class MetricsHandler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=frontend_dir, **kw)

        def do_GET(self):
            if urlparse(self.path).path == "/api/metrics":
                self._send_metrics()
            else:
                super().do_GET()

        def do_OPTIONS(self):
            self.send_response(200)
            self._cors()
            self.end_headers()

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _send_metrics(self):
            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            body = json.dumps({
                "cpu":          round(cpu, 1),
                "ram":          round(mem.percent, 1),
                "gpu":          _gpu_usage(),
                "ram_used_gb":  round(mem.used  / 1024 ** 3, 1),
                "ram_total_gb": round(mem.total / 1024 ** 3, 1),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *a):
            if a and str(a[1]) not in ("200", "304"):
                super().log_message(fmt, *a)

    server = ThreadingHTTPServer(("0.0.0.0", port), MetricsHandler)
    server.daemon_threads = True
    print(f"   [METRICS] Metrics server -> http://localhost:{port}  (Frontend static + /api/metrics)")
    try:
        server.serve_forever()
    except Exception:
        pass


def _launch_metrics_thread(port: int):
    t = threading.Thread(target=_start_metrics_server, args=(port,), daemon=True, name="MetricsServer")
    t.start()
    return t


# ── Banner ────────────────────────────────────────────────────────────────────
BANNER = """
   +============================================================+
   |                                                            |
   |      _  _   _   _____  _   _  ___  ____                   |
   |     | || | / \\  |  _ \\| | | ||_ _|/ ___|                  |
   |  _  | || |/ _ \\ | |_) | | | | | | \\___ \\                  |
   | | |_| || / ___ \\|  _ <| |_| | | |  ___) |                 |
   |  \\___/ |_/_/   \\_\\_| \\_\\\\___/ |___|____/                  |
   |                                                            |
   |                            Mage AI                                 |
   |                                                            |
   +============================================================+
"""


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(BANNER)

    # ── Step 1: metrics server (background thread, non-blocking) ──────────────
    if not args.no_metrics:
        _launch_metrics_thread(args.metrics_port)
        time.sleep(0.3)  # tiny pause so its print arrives before uvicorn's

    # ── Step 2: Main FastAPI server ───────────────────────────────────────────
    print(f"\n   [START] Starting Mage API server ...")
    print(f"   [API]   Main API  -> http://{args.host}:{args.port}")
    print(f"   [UI]    Frontend  -> http://localhost:{args.port}/app/")
    print(f"   [HEALTH] Health   -> http://localhost:{args.port}/health")
    print(f"\n   Press Ctrl+C to stop.\n")

    try:
        import uvicorn
    except ImportError:
        print("[ERROR] uvicorn not installed. Run:  pip install uvicorn[standard]")
        sys.exit(1)

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level="info",
        # Allow any HTTP origin (CORS is handled inside FastAPI too)
        # Forward headers so X-Forwarded-For is respected behind proxies
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
