"""How busy this RepoLens server is: CPU, memory, network and request rate, sampled every few seconds.

Reads /proc (Linux, including containers) so nothing extra is installed; elsewhere the values are simply missing.
History is kept in memory for the last ~20 minutes.
"""

import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

INTERVAL = 5.0
_lock = threading.Lock()
_history: deque[dict] = deque(maxlen=240)
_started = time.time()
_prev: dict = {}
_req: dict[str, Any] = {"n": 0, "errors": 0, "lat": []}


def record_request(status: int, ms: float) -> None:
    with _lock:
        _req["n"] += 1
        if status >= 500:
            _req["errors"] += 1
        if len(_req["lat"]) < 2000:
            _req["lat"].append(ms)


def _read(path: str) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def _cpu_times() -> tuple[float, float] | None:
    line = _read("/proc/stat").split("\n", 1)[0].split()
    if len(line) < 8 or line[0] != "cpu":
        return None
    vals = [float(x) for x in line[1:9]]
    return sum(vals), vals[3] + vals[4]  # total, idle (+ iowait)


def _proc_cpu_seconds() -> float | None:
    parts = _read("/proc/self/stat").rsplit(")", 1)[-1].split()
    if len(parts) < 13:
        return None
    return (float(parts[11]) + float(parts[12])) / os.sysconf("SC_CLK_TCK")


def _meminfo() -> dict[str, int]:
    out = {}
    for ln in _read("/proc/meminfo").splitlines():
        k, _, v = ln.partition(":")
        if v:
            out[k] = int(v.split()[0]) * 1024
    return out


def _cgroup_limit() -> int | None:
    v = _read("/sys/fs/cgroup/memory.max").strip()
    return int(v) if v.isdigit() else None


def _rss() -> int | None:
    for ln in _read("/proc/self/status").splitlines():
        if ln.startswith("VmRSS:"):
            return int(ln.split()[1]) * 1024
    return None


def _net_bytes() -> tuple[int, int] | None:
    rx = tx = 0
    lines = _read("/proc/net/dev").splitlines()[2:]
    for ln in lines:
        name, _, rest = ln.partition(":")
        if name.strip() == "lo" or not rest:
            continue
        f = rest.split()
        rx += int(f[0])
        tx += int(f[8])
    return (rx, tx) if lines else None


def _percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))], 1)


def sample() -> dict:
    """Take one reading and append it to the history."""
    now = time.time()
    cpu, net, proc = _cpu_times(), _net_bytes(), _proc_cpu_seconds()
    with _lock:
        prev, dt = dict(_prev), now - _prev.get("t", now)
        _prev.update(t=now, cpu=cpu, net=net, proc=proc)
        n, errors, lat = _req["n"], _req["errors"], _req["lat"]
        _req.update(n=0, errors=0, lat=[])
    host_cpu = proc_cpu = rx = tx = None
    if dt > 0 and prev.get("cpu") and cpu and cpu[0] > prev["cpu"][0]:
        host_cpu = round(100 * (1 - (cpu[1] - prev["cpu"][1]) / (cpu[0] - prev["cpu"][0])), 1)
    if dt > 0 and prev.get("proc") is not None and proc is not None:
        proc_cpu = round(100 * (proc - prev["proc"]) / dt, 1)  # percent of one core
    if dt > 0 and prev.get("net") and net:
        rx, tx = int((net[0] - prev["net"][0]) / dt), int((net[1] - prev["net"][1]) / dt)
    mem = _meminfo()
    total = _cgroup_limit() or mem.get("MemTotal")
    used = (mem["MemTotal"] - mem["MemAvailable"]) if "MemAvailable" in mem else None
    point = {
        "t": int(now),
        "host_cpu": host_cpu,
        "process_cpu": proc_cpu,
        "mem_used": used,
        "mem_total": mem.get("MemTotal"),
        "process_rss": _rss(),
        "rx_bps": rx,
        "tx_bps": tx,
        "rps": round(n / dt, 2) if dt > 0 else 0,
        "error_rate": round(errors / n, 3) if n else 0,
        "p50_ms": _percentile(lat, 0.5),
        "p95_ms": _percentile(lat, 0.95),
        "container_limit": total if _cgroup_limit() else None,
    }
    with _lock:
        _history.append(point)
    return point


def snapshot() -> dict:
    with _lock:
        hist = list(_history)
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        load = None
    return {
        "available": bool(_read("/proc/stat")),
        "cores": os.cpu_count(),
        "load_average": load,
        "uptime_s": int(time.time() - _started),
        "threads": len(os.listdir("/proc/self/task")) if Path("/proc/self/task").exists() else None,
        "current": hist[-1] if hist else sample(),
        "history": hist,
        "interval_s": INTERVAL,
    }
