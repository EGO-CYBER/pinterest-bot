import os
import platform
import logging

logger = logging.getLogger(__name__)


def _read_proc(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


def get_system_info() -> dict:
    # CPU
    cpu_model = "N/A"
    cpuinfo = _read_proc("/proc/cpuinfo")
    for line in cpuinfo.splitlines():
        if "model name" in line or "Processor" in line:
            cpu_model = line.split(":", 1)[-1].strip()
            break
    if cpu_model == "N/A" and "aarch64" in platform.machine():
        cpu_model = f"ARM {platform.machine()}"

    cpu_count = 0
    for line in cpuinfo.splitlines():
        if line.startswith("processor"):
            cpu_count += 1
    if not cpu_count:
        cpu_count = os.cpu_count() or 1

    # CPU load from /proc/stat
    cpu_percent = 0.0
    stat = _read_proc("/proc/stat")
    for line in stat.splitlines():
        if line.startswith("cpu "):
            parts = line.split()
            if len(parts) >= 5:
                total = sum(int(p) for p in parts[1:])
                idle = int(parts[4])
                if total:
                    cpu_percent = round((1 - idle / total) * 100, 1)
            break

    # RAM from /proc/meminfo
    ram_total = ram_available = 0
    mem = _read_proc("/proc/meminfo")
    for line in mem.splitlines():
        if line.startswith("MemTotal:"):
            ram_total = int(line.split()[1]) * 1024
        elif line.startswith("MemAvailable:"):
            ram_available = int(line.split()[1]) * 1024
    ram_used = ram_total - ram_available
    ram_percent = round((ram_used / ram_total) * 100, 1) if ram_total else 0

    # Disk from statvfs
    try:
        s = os.statvfs("/")
        disk_total = s.f_frsize * s.f_blocks
        disk_free = s.f_frsize * s.f_bavail
        disk_used = disk_total - (s.f_frsize * s.f_bfree)
        disk_percent = round((disk_used / disk_total) * 100, 1) if disk_total else 0
    except Exception:
        disk_total = disk_used = disk_free = disk_percent = 0

    # Uptime
    uptime_str = "N/A"
    upt = _read_proc("/proc/uptime").split()
    if upt:
        seconds = int(float(upt[0]))
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        uptime_str = f"{days}д {hours}ч {minutes}м"

    return {
        "cpu_percent": cpu_percent,
        "cpu_count": cpu_count,
        "cpu_model": cpu_model,
        "hostname": platform.node(),
        "ram_total_gb": round(ram_total / 1024**3, 1),
        "ram_used_gb": round(ram_used / 1024**3, 1),
        "ram_free_gb": round(ram_available / 1024**3, 1),
        "ram_percent": ram_percent,
        "disk_total_gb": round(disk_total / 1024**3, 1),
        "disk_used_gb": round(disk_used / 1024**3, 1),
        "disk_free_gb": round(disk_free / 1024**3, 1),
        "disk_percent": disk_percent,
        "uptime": uptime_str,
        "python_version": platform.python_version(),
        "os": f"{platform.system()} {platform.release()}",
    }
