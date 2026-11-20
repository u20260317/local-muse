"""CPU / 内存采样与硬件降级判定（L430 保护）。

优先使用可选 psutil；未安装时用标准库：Linux 读 /proc，Windows 用 ctypes。
带滞回阈值，避免临界抖动导致任务反复暂停。
"""

# ============================================================
# 【核心模块】基础层 · 硬件采样与降级
# 作用：CPU/内存采样（psutil 优先，标准库兜底），滞回阈值判定 degraded，供调度器暂停派发
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import time
from ctypes import Structure, byref, c_uint32, c_ulong, c_ulonglong

from core import paths

_IS_WINDOWS = paths.IS_WINDOWS


def _psutil_sample():
    try:
        import psutil  # 可选依赖，缺失时自动降级
        return (float(psutil.cpu_percent(interval=0.15)),
                float(psutil.virtual_memory().percent),
                round(psutil.virtual_memory().used / 1024 / 1024, 1),
                round(psutil.virtual_memory().total / 1024 / 1024, 1))
    except Exception:
        return None


class _FileTime(Structure):
    _fields_ = [("dwLowDateTime", c_uint32), ("dwHighDateTime", c_uint32)]


class _MemoryStatusEx(Structure):
    _fields_ = [
        ("dwLength", c_ulong), ("dwMemoryLoad", c_ulong),
        ("ullTotalPhys", c_ulonglong), ("ullAvailPhys", c_ulonglong),
        ("ullTotalPageFile", c_ulonglong), ("ullAvailPageFile", c_ulonglong),
        ("ullTotalVirtual", c_ulonglong), ("ullAvailVirtual", c_ulonglong),
        ("ullAvailExtendedVirtual", c_ulonglong),
    ]


def _ft_value(ft: _FileTime) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def _windows_cpu() -> float:
    """Windows：GetSystemTimes 双采样计算 CPU 使用率。"""
    try:
        import ctypes
        get_times = ctypes.windll.kernel32.GetSystemTimes
        idle1, kernel1, user1 = _FileTime(), _FileTime(), _FileTime()
        idle2, kernel2, user2 = _FileTime(), _FileTime(), _FileTime()
        get_times(byref(idle1), byref(kernel1), byref(user1))
        time.sleep(0.15)
        get_times(byref(idle2), byref(kernel2), byref(user2))
        idle_delta = _ft_value(idle2) - _ft_value(idle1)
        total1 = _ft_value(kernel1) + _ft_value(user1)
        total2 = _ft_value(kernel2) + _ft_value(user2)
        total_delta = total2 - total1
        if total_delta <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta)))
    except Exception:
        return 0.0


def _linux_cpu() -> float:
    """Linux：/proc/stat 双采样。"""
    def read():
        with open("/proc/stat", encoding="utf-8") as f:
            parts = [float(x) for x in f.readline().split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        return sum(parts), idle
    try:
        total1, idle1 = read()
        time.sleep(0.15)
        total2, idle2 = read()
        delta = total2 - total1
        return max(0.0, min(100.0, 100.0 * (1.0 - (idle2 - idle1) / delta))) if delta > 0 else 0.0
    except Exception:
        return 0.0


def _memory() -> tuple[float, float, float]:
    """返回 (使用百分比, 已用 MB, 总 MB)。"""
    if _IS_WINDOWS:
        try:
            import ctypes
            stat = _MemoryStatusEx()
            stat.dwLength = 64
            ctypes.windll.kernel32.GlobalMemoryStatusEx(byref(stat))
            total = stat.ullTotalPhys / 1024 / 1024
            used = total - stat.ullAvailPhys / 1024 / 1024
            return float(stat.dwMemoryLoad), round(used, 1), round(total, 1)
        except Exception:
            return 0.0, 0.0, 0.0
    try:
        info = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                key, _, value = line.partition(":")
                info[key] = float(value.strip().split()[0]) / 1024  # kB -> MB
        total = info.get("MemTotal", 0.0)
        avail = info.get("MemAvailable", total)
        used = total - avail
        pct = (used / total * 100) if total else 0.0
        return round(pct, 1), round(used, 1), round(total, 1)
    except Exception:
        return 0.0, 0.0, 0.0


class HardwareMonitor:
    """周期采样 + 滞回降级；只暂停「从队列取新任务」，不杀任何运行中任务。"""

    def __init__(self, config) -> None:
        self._config = config
        self._degraded = False
        self._last: dict = {}
        self._reason = ""

    @property
    def thresholds(self) -> tuple[float, float, float]:
        hw = self._config.section("hardware")
        return (float(hw.get("cpu_threshold", 85)),
                float(hw.get("memory_threshold", 85)),
                float(hw.get("recovery_margin", 10)))

    def sample(self) -> dict:
        ps = _psutil_sample()
        if ps is not None:
            cpu, mem, used_mb, total_mb = ps
        else:
            cpu = _windows_cpu() if _IS_WINDOWS else _linux_cpu()
            mem, used_mb, total_mb = _memory()
        return {
            "cpu_percent": round(cpu, 1),
            "memory_percent": round(mem, 1),
            "memory_used_mb": used_mb,
            "memory_total_mb": total_mb,
            "platform": "windows" if _IS_WINDOWS else "posix",
            "sampled_at": time.time(),
        }

    def update(self) -> dict:
        """执行一次采样并按滞回规则更新降级状态。"""
        cpu_thr, mem_thr, margin = self.thresholds
        data = self.sample()
        cpu, mem = data["cpu_percent"], data["memory_percent"]
        over = (cpu >= cpu_thr) or (mem >= mem_thr)
        recovered = (cpu <= cpu_thr - margin) and (mem <= mem_thr - margin)
        changed = False
        if not self._degraded and over:
            self._degraded, changed = True, True
            self._reason = f"CPU {cpu}% / 内存 {mem}% 超过警戒线，暂停派发新任务"
        elif self._degraded and recovered:
            self._degraded, changed = False, True
            self._reason = f"资源回落至 CPU {cpu}% / 内存 {mem}%，恢复调度"
        data.update({
            "degraded": self._degraded,
            "degraded_changed": changed,
            "reason": self._reason,
            "cpu_threshold": cpu_thr,
            "memory_threshold": mem_thr,
        })
        self._last = data
        return data

    def degraded(self) -> bool:
        return self._degraded

    def snapshot(self) -> dict:
        return dict(self._last) if self._last else self.update()
