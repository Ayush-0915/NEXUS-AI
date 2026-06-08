# actions/system_info.py

import os
import sys
import platform
import socket
import subprocess
import json
import re
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None


def is_system_info_query(text: str) -> bool:
    """
    Detects if the user's input matches hardware/system specs queries.
    """
    if not text:
        return False
    t = text.lower()

    # Exact check for requested phrases and words
    triggers = [
        "laptop specs",
        "laptop specifications",
        "system specs",
        "system specifications",
        "pc specifications",
        "pc specs",
        "computer information",
        "computer info",
        "hardware details",
        "hardware info",
        "my ram",
        "my cpu",
        "my gpu",
        "battery health",
        "storage details",
        "cpu temperature",
        "gpu temperature",
        "system details",
        "device information",
        "motherboard model"
    ]

    if any(trigger in t for trigger in triggers):
        return True

    # Also match questions containing specs/hardware keywords combined with specifiers
    keywords = ["ram", "cpu", "gpu", "vram", "battery", "storage", "specifications", "specs", "temperature", "temp"]
    has_specifier = any(x in t for x in ["my ", "what is ", "how much ", "which ", "show ", "get ", "check "])
    has_keyword = any(k in t for k in keywords)

    if has_specifier and has_keyword:
        return True

    return False


def get_cpu_temp() -> str:
    try:
        cmd = "Get-WmiObject -Namespace root/wmi -Class MSAcpi_ThermalZoneTemperature | Select-Object CurrentTemperature | Format-List"
        res = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            m = re.search(r"CurrentTemperature\s*:\s*(\d+)", res.stdout)
            if m:
                val = int(m.group(1))
                temp = (val / 10.0) - 273.15
                return f"{round(temp, 1)} °C"
    except Exception:
        pass
    return "N/A"


def get_ram_details() -> tuple[str, str]:
    speed = "N/A"
    slots_str = "N/A"
    try:
        cmd = "Get-CimInstance Win32_PhysicalMemory | Select-Object DeviceLocator, Capacity, Speed | ConvertTo-Json"
        res = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True, timeout=4)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            if isinstance(data, dict):
                data = [data]
            slots = []
            for item in data:
                cap = item.get("Capacity") or 0
                cap_gb = round(cap / (1024**3))
                loc = item.get("DeviceLocator") or "Unknown"
                slots.append(f"{loc}: {cap_gb} GB")
                sp = item.get("Speed")
                if sp:
                    speed = f"{sp} MHz"
            if slots:
                slots_str = f"{len(slots)} populated ({', '.join(slots)})"
    except Exception:
        pass
    return speed, slots_str


def get_nvidia_details() -> tuple[str, str, str] | None:
    try:
        cmd = "nvidia-smi --query-gpu=name,memory.total,temperature.gpu --format=csv,noheader,nounits"
        res = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.strip().split(",")
            name = parts[0].strip().replace("GeForce ", "")
            vram = f"{round(float(parts[1].strip()) / 1024.0, 1)} GB"
            temp = f"{parts[2].strip()} °C"
            return name, vram, temp
    except Exception:
        pass
    return None


def get_all_gpus() -> list[dict]:
    gpus = []
    nvidia = get_nvidia_details()
    if nvidia:
        gpus.append({
            "name": nvidia[0],
            "vram": nvidia[1],
            "temp": nvidia[2]
        })

    try:
        cmd = "Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM | ConvertTo-Json"
        res = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True, timeout=4)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            if isinstance(data, dict):
                data = [data]
            for item in data:
                name = item.get("Name", "")
                cleaned_name = name.replace("GeForce ", "").strip()
                if any(cleaned_name in g["name"] or g["name"] in cleaned_name for g in gpus):
                    continue
                ram = item.get("AdapterRAM") or 0
                ram = abs(int(ram))
                vram_str = "N/A"
                if ram > 0:
                    vram_str = f"{round(ram / (1024**3), 1)} GB"
                gpus.append({
                    "name": cleaned_name,
                    "vram": vram_str,
                    "temp": "N/A"
                })
    except Exception:
        pass
    return gpus


def get_disk_details() -> tuple[str, str, str]:
    model, media_type, health = "Unknown Disk", "SSD", "Healthy"
    try:
        cmd = "Get-PhysicalDisk | Select-Object Model, MediaType, OperationalStatus, HealthStatus | ConvertTo-Json"
        res = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True, timeout=4)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            if isinstance(data, dict):
                data = [data]
            if data:
                disk = data[0]
                model = disk.get("Model", "Unknown").strip()
                media_type = disk.get("MediaType", "SSD").strip()
                h_status = disk.get("HealthStatus", "Healthy").strip()
                op_status = disk.get("OperationalStatus", "OK").strip()
                health = f"{h_status} (Status: {op_status})"
    except Exception:
        pass
    return model, media_type, health


def get_os_details() -> tuple[str, str, str]:
    try:
        cmd = "Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture | ConvertTo-Json"
        res = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True, timeout=4)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            caption = data.get("Caption", "").replace("Microsoft ", "").strip()
            version = data.get("Version", "")
            arch = data.get("OSArchitecture", "")
            return caption, version, arch
    except Exception:
        pass
    import platform
    caption = f"Windows {platform.release()}"
    version = platform.version()
    arch = "64-bit" if sys.maxsize > 2**32 else "32-bit"
    return caption, version, arch


def get_device_details() -> tuple[str, str, str, str, str]:
    name = socket.gethostname()
    manufacturer = "Unknown"
    model = "Unknown"
    board_man = "Unknown"
    board_model = "Unknown"

    try:
        cmd1 = "Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model | ConvertTo-Json"
        res1 = subprocess.run(["powershell", "-Command", cmd1], capture_output=True, text=True, timeout=4)
        if res1.returncode == 0 and res1.stdout.strip():
            data = json.loads(res1.stdout.strip())
            manufacturer = data.get("Manufacturer", "Unknown").strip()
            model = data.get("Model", "Unknown").strip()

        cmd2 = "Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer, Product | ConvertTo-Json"
        res2 = subprocess.run(["powershell", "-Command", cmd2], capture_output=True, text=True, timeout=4)
        if res2.returncode == 0 and res2.stdout.strip():
            data = json.loads(res2.stdout.strip())
            board_man = data.get("Manufacturer", "Unknown").strip()
            board_model = data.get("Product", "Unknown").strip()
    except Exception:
        pass

    return name, manufacturer, model, board_man, board_model


def get_battery_details() -> dict | None:
    if psutil is None:
        return None
    battery = psutil.sensors_battery()
    if not battery:
        return None

    pct = battery.percent
    status = "Charging" if battery.power_plugged else "Discharging"

    design = "N/A"
    full = "N/A"
    health_pct = "N/A"
    try:
        # Design Capacity
        cmd1 = "Get-WmiObject -Namespace root/wmi -Class BatteryStaticData | Select-Object DesignedCapacity | Format-List"
        res1 = subprocess.run(["powershell", "-Command", cmd1], capture_output=True, text=True, timeout=3)
        d_val = None
        if res1.returncode == 0:
            m = re.search(r"DesignedCapacity\s*:\s*(\d+)", res1.stdout)
            if m:
                d_val = int(m.group(1))
                design = f"{d_val} mWh"

        # Full Charge Capacity
        cmd2 = "Get-WmiObject -Namespace root/wmi -Class BatteryFullChargedCapacity | Select-Object FullChargedCapacity | Format-List"
        res2 = subprocess.run(["powershell", "-Command", cmd2], capture_output=True, text=True, timeout=3)
        f_val = None
        if res2.returncode == 0:
            m = re.search(r"FullChargedCapacity\s*:\s*(\d+)", res2.stdout)
            if m:
                f_val = int(m.group(1))
                full = f"{f_val} mWh"

        if d_val and f_val:
            health_pct = f"{round((f_val / d_val) * 100, 1)}%"
    except Exception:
        pass

    return {
        "percent": pct,
        "status": status,
        "design": design,
        "full": full,
        "health": health_pct
    }


def get_system_info(parameters: dict = None, player=None) -> str:
    """
    Action that returns a clean, detailed, human-readable report.
    """
    # CPU
    cpu_reg = ""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        cpu_reg = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except Exception:
        pass
    cpu_name = cpu_reg or platform.processor()
    cpu_name = cpu_name.replace("(R)", "").replace("(TM)", "")
    cpu_name = " ".join(cpu_name.split())

    cores = psutil.cpu_count(logical=False) if psutil else "Unknown"
    threads = psutil.cpu_count(logical=True) if psutil else "Unknown"
    cpu_usage = psutil.cpu_percent(interval=0.1) if psutil else "Unknown"
    cpu_temp = get_cpu_temp()

    # RAM
    if psutil:
        mem = psutil.virtual_memory()
        total_ram = f"{round(mem.total / (1024**3))} GB"
        avail_ram = f"{round(mem.available / (1024**3), 1)} GB"
    else:
        total_ram = "Unknown"
        avail_ram = "Unknown"
    ram_speed, ram_slots = get_ram_details()

    # GPU
    gpus = get_all_gpus()
    gpu_lines = []
    if gpus:
        for idx, g in enumerate(gpus):
            prefix = f"GPU {idx+1}: " if len(gpus) > 1 else ""
            gpu_lines.append(f"{prefix}{g['name']} ({g['vram']}) | Temp: {g['temp']}")
        gpu_str = "\n".join(gpu_lines)
    else:
        gpu_str = "Unknown"

    # Storage
    if psutil:
        try:
            usage = psutil.disk_usage('C:\\')
            disk_total = f"{round(usage.total / (1024**3))} GB"
            disk_free = f"{round(usage.free / (1024**3))} GB"
        except Exception:
            disk_total = "Unknown"
            disk_free = "Unknown"
    else:
        disk_total = "Unknown"
        disk_free = "Unknown"
    disk_model, disk_media, disk_health = get_disk_details()

    # OS
    os_caption, os_build, os_arch = get_os_details()

    # Device Info
    dev_name, manufacturer, model, board_man, board_model = get_device_details()

    # Battery
    battery = get_battery_details()

    # Network
    local_ip = "Unknown"
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        pass
    hostname = socket.gethostname()

    # Format human-readable System Report
    lines = [
        "==================================================",
        "           NEXUS AI SYSTEM REPORT",
        "==================================================",
        "",
        "DEVICE INFORMATION",
        "------------------",
        f"Device Name  : {dev_name}",
        f"Manufacturer : {manufacturer}",
        f"Model        : {model}",
        f"Motherboard  : {board_man} {board_model}",
        "",
        "CPU",
        "---",
        f"Model        : {cpu_name}",
        f"Cores/Threads: {cores} Cores / {threads} Threads",
        f"Current Usage: {cpu_usage}%",
        f"Temperature  : {cpu_temp}",
        "",
        "RAM",
        "---",
        f"Total RAM    : {total_ram}",
        f"Available    : {avail_ram}",
        f"Speed        : {ram_speed}",
        f"Slots        : {ram_slots}",
        "",
        "GPU",
        "---",
        gpu_str,
        "",
        "STORAGE",
        "-------",
        f"Disk Model   : {disk_model}",
        f"Media Type   : {disk_media}",
        f"Total Size   : {disk_total}",
        f"Free Space   : {disk_free}",
        f"Disk Health  : {disk_health}",
        "",
        "OPERATING SYSTEM",
        "----------------",
        f"OS Version   : {os_caption}",
        f"Architecture : {os_arch}",
        f"Build Number : {os_build}",
    ]

    if battery:
        lines.extend([
            "",
            "BATTERY",
            "-------",
            f"Level        : {battery['percent']}%",
            f"Status       : {battery['status']}",
            f"Design Cap   : {battery['design']}",
            f"Full Chg Cap : {battery['full']}",
            f"Health       : {battery['health']}",
        ])
    else:
        lines.extend([
            "",
            "BATTERY",
            "-------",
            "Battery      : N/A (Desktop PC / No Battery)"
        ])

    lines.extend([
        "",
        "NETWORK",
        "-------",
        f"Hostname     : {hostname}",
        f"Local IP     : {local_ip}",
        "",
        "=================================================="
    ])

    report = "\n".join(lines)
    
    if player:
        try:
            player.write_log(f"NEXUS AI: System specifications gathered.")
        except Exception:
            pass

    return report


APP_NAME_MAP = {
    "chrome.exe": "Chrome",
    "code.exe": "VS Code",
    "spotify.exe": "Spotify",
    "discord.exe": "Discord",
    "steam.exe": "Steam",
    "firefox.exe": "Firefox",
    "msedge.exe": "Edge",
    "explorer.exe": "Windows Explorer",
    "notepad.exe": "Notepad",
    "slack.exe": "Slack",
    "teams.exe": "Teams",
    "zoom.exe": "Zoom",
    "python.exe": "Python Interpreter",
    "py.exe": "Python Launcher",
    "powershell.exe": "PowerShell",
    "cmd.exe": "Command Prompt",
}


# Intent matchers for system commands
def is_performance_monitor_query(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    triggers = [
        "monitor my system",
        "show system performance",
        "live performance stats",
        "open performance monitor",
        "close performance monitor",
        "start monitoring",
        "stop monitoring"
    ]
    return any(trigger in t for trigger in triggers)


def is_system_health_query(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    triggers = [
        "check my pc health",
        "nexus, check my pc health",
        "system health check",
        "check pc health",
        "diagnose my pc health",
        "how healthy is my pc",
        "pc health score"
    ]
    return any(trigger in t for trigger in triggers)


def is_app_detection_query(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    triggers = [
        "what apps are running",
        "show active programs",
        "list running applications",
        "running apps",
        "active apps",
        "running processes"
    ]
    return any(trigger in t for trigger in triggers)


def is_hardware_recommendation_query(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    triggers = [
        "can i upgrade my ram",
        "is my ssd upgradeable",
        "what ram should i buy",
        "is my laptop good for ai",
        "is my laptop good for ml",
        "run llama 3 locally",
        "good for machine learning",
        "can i run stable diffusion",
        "can i upgrade my ssd",
        "ssd upgradeable"
    ]
    return any(trigger in t for trigger in triggers)


def is_system_diagnostics_query(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    triggers = [
        "why is my laptop slow",
        "diagnose my pc",
        "diagnose my computer",
        "analyze system performance",
        "system diagnostics",
        "pc troubleshooting"
    ]
    return any(trigger in t for trigger in triggers)


def is_storage_analyzer_query(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    triggers = [
        "what is using my storage",
        "analyze disk usage",
        "largest folders",
        "biggest files",
        "what are the biggest files",
        "storage analyzer"
    ]
    return any(trigger in t for trigger in triggers)


# Helper to get Window Titles from Process IDs on Windows
def get_pid_to_window_titles() -> dict[int, str]:
    pid_to_title = {}
    if platform.system() != "Windows":
        return pid_to_title
    try:
        import ctypes
        from ctypes import wintypes
        
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32 = ctypes.windll.user32
        
        def enum_windows_callback(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buffer, length + 1)
                    title = buffer.value
                    if title and title.strip():
                        existing = pid_to_title.get(pid.value)
                        if not existing or len(title) > len(existing):
                            pid_to_title[pid.value] = title
            return True
            
        user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)
    except Exception:
        pass
    return pid_to_title


# Real-time metrics
def get_performance_metrics(parameters: dict = None, player=None) -> str:
    cpu_usage = psutil.cpu_percent(interval=0.1) if psutil else 0.0
    cpu_temp = get_cpu_temp()
    
    ram_pct = 0.0
    ram_used = 0.0
    ram_total = 0.0
    if psutil:
        mem = psutil.virtual_memory()
        ram_pct = mem.percent
        ram_used = mem.used / (1024**3)
        ram_total = mem.total / (1024**3)
        
    gpu_usage = "N/A"
    gpu_temp = "N/A"
    nvidia = get_nvidia_details()
    if nvidia:
        gpu_temp = nvidia[2]
        try:
            cmd = "nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits"
            res = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                gpu_usage = f"{res.stdout.strip()}%"
        except Exception:
            pass
            
    battery_str = "N/A"
    if psutil:
        bat = psutil.sensors_battery()
        if bat:
            battery_str = f"{bat.percent}% ({'Charging' if bat.power_plugged else 'Discharging'})"
            
    net_status = "Disconnected"
    try:
        socket.setdefaulttimeout(1)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        net_status = "Connected (Internet Access)"
    except Exception:
        net_status = "Disconnected (No Internet)"
        
    lines = [
        "==================================================",
        "        NEXUS AI PERFORMANCE METRICS",
        "==================================================",
        f"CPU Usage   : {cpu_usage:.1f}%",
        f"CPU Temp    : {cpu_temp}",
        f"RAM Usage   : {ram_pct:.1f}% ({ram_used:.1f} GB / {ram_total:.1f} GB)",
        f"GPU Usage   : {gpu_usage}",
        f"GPU Temp    : {gpu_temp}",
        f"Battery     : {battery_str}",
        f"Network     : {net_status}",
        "=================================================="
    ]
    return "\n".join(lines)


# PC Health Check
def check_system_health(parameters: dict = None, player=None) -> str:
    score = 10.0
    recommendations = []
    
    # 1. CPU Temperature check
    cpu_temp_val = -1.0
    cpu_temp = get_cpu_temp()
    if cpu_temp != "N/A":
        try:
            cpu_temp_val = float(cpu_temp.replace("°C", "").strip())
            if cpu_temp_val > 85:
                score -= 1.5
                recommendations.append(f"CPU temperature is critical ({cpu_temp_val}°C). Clean dust from vents or check cooling.")
            elif cpu_temp_val > 75:
                score -= 0.8
                recommendations.append(f"CPU temperature is high ({cpu_temp_val}°C). Avoid heavy tasks and ensure ventilation.")
        except Exception:
            pass
            
    # 2. RAM Pressure check
    ram_used_pct = 0.0
    if psutil:
        mem = psutil.virtual_memory()
        ram_used_pct = mem.percent
        if ram_used_pct > 90:
            score -= 1.5
            recommendations.append(f"RAM pressure is critical ({ram_used_pct}%). Close resource-heavy applications immediately.")
        elif ram_used_pct > 80:
            score -= 0.8
            recommendations.append(f"RAM usage is high ({ram_used_pct}%). Consider closing background applications.")
            
    # 3. SSD/Disk health check
    disk_model, disk_media, disk_health = get_disk_details()
    if "Healthy" not in disk_health and "OK" not in disk_health and disk_health != "Unknown":
        score -= 2.0
        recommendations.append(f"Storage drive status warning: '{disk_health}'. Back up your data immediately.")
        
    # 4. Storage headroom check
    free_pct = 100.0
    free_gb = 999.0
    if psutil:
        try:
            usage = psutil.disk_usage('C:\\')
            free_pct = (usage.free / usage.total) * 100
            free_gb = usage.free / (1024**3)
            if free_gb < 10:
                score -= 1.5
                recommendations.append(f"C: drive is extremely low on space ({free_gb:.1f} GB free). Run Storage Sense or delete unused files.")
            elif free_gb < 20 or free_pct < 15:
                score -= 0.7
                recommendations.append(f"C: drive free space is low ({free_gb:.1f} GB free, {free_pct:.1f}%). Consolidate files.")
        except Exception:
            pass
            
    # 5. Battery health check
    bat = get_battery_details()
    bat_health_val = 100.0
    if bat and bat.get("health") and bat["health"] != "N/A":
        try:
            bat_health_val = float(bat["health"].replace("%", "").strip())
            if bat_health_val < 50:
                score -= 2.0
                recommendations.append(f"Battery health is critical ({bat_health_val}%). The battery needs replacement.")
            elif bat_health_val < 80:
                score -= 1.2
                recommendations.append(f"Battery health is below 80% ({bat_health_val}%). Consider power saving settings.")
        except Exception:
            pass
            
    # 6. Running processes check for high resource usage
    high_ram_proc = []
    if psutil:
        try:
            for p in psutil.process_iter(['pid', 'name', 'memory_info']):
                try:
                    mem_gb = p.info['memory_info'].rss / (1024**3)
                    if mem_gb > 4.0:
                        high_ram_proc.append(f"{p.info['name']} ({mem_gb:.1f} GB)")
                except Exception:
                    pass
        except Exception:
            pass
            
    if high_ram_proc:
        score -= 0.8
        recommendations.append(f"High memory application detected: {', '.join(high_ram_proc)}. Consider restarting these apps.")

    # Bound score to [0.0, 10.0]
    score = max(0.0, min(10.0, score))
    score = round(score, 1)
    
    # Category
    if score >= 9.0:
        cat = "Excellent"
    elif score >= 7.0:
        cat = "Good"
    elif score >= 5.0:
        cat = "Fair"
    else:
        cat = "Poor"
        
    cpu_status = "Good"
    if cpu_temp_val > 85:
        cpu_status = "Critical"
    elif cpu_temp_val > 75:
        cpu_status = "Fair"
        
    ram_status = "Good"
    if ram_used_pct > 90:
        ram_status = "Critical"
    elif ram_used_pct > 80:
        ram_status = "Fair"
        
    bat_status = "Good"
    if bat_health_val < 50:
        bat_status = "Poor"
    elif bat_health_val < 80:
        bat_status = "Fair"
        
    storage_status = "Good"
    if free_gb < 10 or "Healthy" not in disk_health:
        storage_status = "Poor"
    elif free_gb < 20:
        storage_status = "Fair"
    elif free_gb > 100:
        storage_status = "Excellent"
        
    lines = [
        "==================================================",
        "           NEXUS AI SYSTEM HEALTH CHECK",
        "==================================================",
        f"Overall Health Score: {score} / 10 ({cat})",
        "",
        f"CPU      : {cpu_status} ({cpu_temp if cpu_temp != 'N/A' else 'N/A'})",
        f"RAM      : {ram_status} ({ram_used_pct:.0f}% Used)",
        f"Battery  : {bat_status} ({bat['health'] if bat and bat.get('health') else 'N/A'})",
        f"Storage  : {storage_status} ({disk_health}, {free_gb:.1f} GB free)",
        "",
        "Recommendations:"
    ]
    if recommendations:
        for rec in recommendations:
            lines.append(f"- {rec}")
    else:
        lines.append("- All systems running smoothly. No recommendations.")
    lines.append("==================================================")
    
    return "\n".join(lines)


# App Detection
def get_running_apps(parameters: dict = None, player=None) -> str:
    if not psutil:
        return "psutil package is not available to retrieve running apps."
        
    pid_to_title = get_pid_to_window_titles()
    app_data = {}
    
    for p in psutil.process_iter(['pid', 'name', 'memory_info', 'cpu_percent']):
        try:
            info = p.info
            name = info['name']
            pid = info['pid']
            if not name:
                continue
                
            lower_name = name.lower()
            display_name = APP_NAME_MAP.get(lower_name, name)
            
            cpu = info['cpu_percent'] or 0.0
            mem = info['memory_info'].rss if info['memory_info'] else 0
            title = pid_to_title.get(pid, "")
            
            if display_name in app_data:
                app_data[display_name]['cpu'] += cpu
                app_data[display_name]['mem'] += mem
                if title and not app_data[display_name]['title']:
                    app_data[display_name]['title'] = title
            else:
                app_data[display_name] = {
                    'name': display_name,
                    'cpu': cpu,
                    'mem': mem,
                    'title': title
                }
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
            
    sorted_apps = sorted(app_data.values(), key=lambda x: x['mem'], reverse=True)
    
    lines = [
        "=============================================================",
        "                 NEXUS AI RUNNING APPLICATIONS",
        "=============================================================",
        f"{'Application':<22} {'CPU%':<8} {'Memory':<12} {'Window Title (Active)'}",
        "-" * 61
    ]
    
    for app in sorted_apps[:10]:
        mem_val = app['mem']
        if mem_val > 1024**3:
            mem_str = f"{mem_val / 1024**3:.2f} GB"
        else:
            mem_str = f"{mem_val / 1024**2:.1f} MB"
            
        title_str = app['title'] if app['title'] else ""
        if len(title_str) > 24:
            title_str = title_str[:21] + "..."
            
        cpu_str = f"{app['cpu']:.1f}%"
        lines.append(f"{app['name']:<22} {cpu_str:<8} {mem_str:<12} {title_str}")
        
    lines.append("-" * 61)
    lines.append(f"Total system processes running: {len(psutil.pids()) if psutil else 'N/A'}")
    lines.append("=============================================================")
    
    return "\n".join(lines)


# Hardware Recommendation Engine
def get_hardware_recommendations(parameters: dict = None, player=None) -> str:
    parameters = parameters or {}
    query = parameters.get("query", "").lower()
    
    cpu_reg = ""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        cpu_reg = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except Exception:
        pass
    cpu_name = cpu_reg or platform.processor()
    cpu_name = cpu_name.replace("(R)", "").replace("(TM)", "")
    cpu_name = " ".join(cpu_name.split())
    
    total_ram_gb = 0
    if psutil:
        mem = psutil.virtual_memory()
        total_ram_gb = round(mem.total / (1024**3))
    ram_speed, ram_slots = get_ram_details()
    
    gpus = get_all_gpus()
    nvidia_gpu = None
    nvidia_vram_gb = 0.0
    for g in gpus:
        if "nvidia" in g["name"].lower() or get_nvidia_details():
            nvidia_gpu = g
            vram_str = g.get("vram", "0")
            try:
                m = re.search(r"([\d.]+)", vram_str)
                if m:
                    nvidia_vram_gb = float(m.group(1))
            except Exception:
                pass
            break
            
    disk_model, disk_media, disk_health = get_disk_details()
    
    populated = 0
    total_slots = 2
    if ram_slots != "N/A":
        try:
            m = re.search(r"(\d+)\s+populated", ram_slots)
            if m:
                populated = int(m.group(1))
        except Exception:
            pass
            
    if total_ram_gb < 16:
        if populated < total_slots:
            ram_upgrade = f"We recommend upgrading your RAM to at least 16GB. You have open slots ({populated}/{total_slots} populated). Adding a matching stick of {ram_speed} RAM is easy and cheap."
        else:
            ram_upgrade = "We recommend upgrading to 16GB or 32GB RAM. Since your slots are fully populated, you will need to replace existing modules with a higher capacity kit."
    else:
        ram_upgrade = f"Your current RAM ({total_ram_gb} GB @ {ram_speed}) is sufficient for most daily tasks. No urgent RAM upgrade is required."
        
    if "ssd" not in disk_media.lower():
        storage_upgrade = "Your primary system drive is not an SSD. Upgrading your boot drive to a Solid State Drive (SSD) will give you a major speed upgrade."
    else:
        storage_upgrade = "You have an SSD, which is excellent. If you are running out of storage, consider adding a secondary NVMe M.2 SSD (e.g. Crucial P3 or Samsung 980) or replacing your main drive with a 1TB/2TB module."
        
    ai_ml_analysis = ""
    run_llama3 = ""
    run_sd = ""
    
    if nvidia_gpu:
        name = nvidia_gpu["name"]
        ai_ml_analysis = f"Your system has an NVIDIA GPU ({name} with {nvidia_vram_gb:.1f} GB VRAM) which supports CUDA. This makes it suitable for local AI/ML tasks!"
        
        if nvidia_vram_gb >= 12:
            run_llama3 = "Yes, you can run Llama 3 (8B) locally at fast speeds (40+ tokens/sec) and load quantized Llama 3 (70B)."
            run_sd = "Yes, you can run Stable Diffusion XL (SDXL) and Stable Diffusion 1.5 locally at excellent generation speeds."
        elif nvidia_vram_gb >= 8:
            run_llama3 = "Yes, you can run Llama 3 (8B) locally at good speeds using 4-bit or 8-bit quantization."
            run_sd = "Yes, you can run Stable Diffusion 1.5 at good speeds, and SDXL locally with optimized low-vram memory profiles."
        elif nvidia_vram_gb >= 6:
            run_llama3 = "Yes, you can run Llama 3 (8B) locally using 4-bit quantization, though response times will be slightly slower."
            run_sd = "Yes, you can run Stable Diffusion 1.5 locally. SDXL might exceed VRAM limits unless optimized low-vram settings are used."
        else:
            run_llama3 = f"Your GPU VRAM ({nvidia_vram_gb:.1f} GB) is a bit low for local LLMs. You can run small models like Llama 3 8B (highly quantized) using hybrid CPU/GPU offloading, but speed will be limited."
            run_sd = "Stable Diffusion 1.5 is barely runnable in low-vram mode, and SDXL will not fit in VRAM."
    else:
        ai_ml_analysis = "Your system does not have an NVIDIA GPU (CUDA active). Local machine learning training and deep learning models are not recommended on CPU alone."
        run_llama3 = "You can run Llama 3 8B (quantized) locally using CPU inference with projects like llama.cpp / Ollama, but it will be slow (approx. 2-5 tokens/sec). Using cloud APIs (Gemini/OpenRouter) is highly recommended."
        run_sd = "Running Stable Diffusion on CPU is extremely slow (taking several minutes per image) and is not recommended."

    lines = [
        "=================================================================",
        "             NEXUS AI HARDWARE RECOMMENDATION ENGINE             ",
        "=================================================================",
        "CURRENT SPECIFICATIONS",
        f"- CPU: {cpu_name}",
        f"- RAM: {total_ram_gb} GB (@ {ram_speed}, Slots: {ram_slots})",
        f"- GPU: {gpus[0]['name'] if gpus else 'N/A'} (NVIDIA CUDA: {'Yes' if nvidia_gpu else 'No'})",
        f"- Disk: {disk_model} ({disk_media}, Status: {disk_health})",
        "",
        "UPGRADE SUGGESTIONS",
        f"1. RAM Upgrade   : {ram_upgrade}",
        f"2. SSD Upgrade   : {storage_upgrade}",
        "",
        "LOCAL AI/ML SUITABILITY ANALYSIS",
        f"- Compatibility  : {ai_ml_analysis}",
        f"- Local Llama 3  : {run_llama3}",
        f"- Stable Diffusion: {run_sd}",
        "================================================================="
    ]
    return "\n".join(lines)


# System Diagnostics
def diagnose_system(parameters: dict = None, player=None) -> str:
    issues = []
    
    cpu_pct = psutil.cpu_percent(interval=0.1) if psutil else 0.0
    if cpu_pct > 80:
        confidence = cpu_pct
        issues.append({
            "confidence": confidence,
            "issue": f"High CPU Utilization ({cpu_pct:.1f}%)",
            "cause": "One or more active processes are putting high load on the processor, leaving little scheduling time for other apps.",
            "recommendation": "Open Task Manager, sort by CPU %, and terminate the offending processes."
        })
        
    ram_pct = 0.0
    if psutil:
        mem = psutil.virtual_memory()
        ram_pct = mem.percent
        if ram_pct > 85:
            confidence = ram_pct
            issues.append({
                "confidence": confidence,
                "issue": f"High RAM Pressure ({ram_pct:.1f}%)",
                "cause": "Your system memory is nearly full. Windows is likely swapping memory to disk, causing disk thrashing and lags.",
                "recommendation": "Close unused applications, clean up browser tabs, or consider upgrading your physical RAM."
            })
            
    if psutil:
        try:
            usage = psutil.disk_usage('C:\\')
            free_gb = usage.free / (1024**3)
            free_pct = (usage.free / usage.total) * 100
            if free_gb < 15:
                confidence = 90 - min(80, free_gb * 5)
                issues.append({
                    "confidence": confidence,
                    "issue": f"Low Free Storage on C: ({free_gb:.1f} GB / {free_pct:.1f}% free)",
                    "cause": "Windows requires empty disk space for paging, update operations, and cache directories.",
                    "recommendation": "Delete temporary files, empty your Recycle Bin, or run Windows Disk Cleanup."
                })
        except Exception:
            pass
            
    startup_count = 0
    try:
        cmd = "Get-CimInstance Win32_StartupCommand | Select-Object Name | ConvertTo-Json"
        res = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True, timeout=4)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            startup_count = len(data) if isinstance(data, list) else 1
    except Exception:
        pass
        
    if startup_count > 10:
        confidence = min(85.0, 40.0 + startup_count * 2.0)
        issues.append({
            "confidence": confidence,
            "issue": f"Excessive Startup Applications ({startup_count} apps)",
            "cause": "Many applications launch automatically during boot, running in the background, consuming CPU and RAM.",
            "recommendation": "Open Task Manager, go to 'Startup apps' tab, and disable unnecessary programs."
        })
        
    cpu_temp_val = -1.0
    cpu_temp = get_cpu_temp()
    if cpu_temp != "N/A":
        try:
            cpu_temp_val = float(cpu_temp.replace("°C", "").strip())
        except Exception:
            pass
    if cpu_temp_val > 80:
        confidence = cpu_temp_val
        issues.append({
            "confidence": confidence,
            "issue": f"High CPU Temperature ({cpu_temp_val}°C)",
            "cause": "Your processor is running hot. Windows or BIOS may trigger thermal throttling, deliberately slowing down your CPU to prevent damage.",
            "recommendation": "Clean dust from laptop vents, ensure ventilation, and check that cooling fans are working."
        })
        
    bat = get_battery_details()
    bat_health_val = 100.0
    if bat and bat.get("health") and bat["health"] != "N/A":
        try:
            bat_health_val = float(bat["health"].replace("%", "").strip())
        except Exception:
            pass
    if bat_health_val < 75:
        confidence = 100 - bat_health_val
        issues.append({
            "confidence": confidence,
            "issue": f"Battery Degradation ({bat_health_val}% Health)",
            "cause": "The lithium-ion battery capacity has significantly degraded. Windows might restrict performance in battery mode to prolong runtime.",
            "recommendation": "Keep the laptop plugged in during heavy tasks, or consider replacing the battery."
        })

    sorted_issues = sorted(issues, key=lambda x: x["confidence"], reverse=True)
    
    lines = [
        "=================================================================",
        "                   NEXUS AI SYSTEM DIAGNOSTIC                    ",
        "=================================================================",
        f"Diagnostics complete: Checked CPU, RAM, Storage, Startup, Thermals, Battery.",
        f"Found {len(sorted_issues)} active diagnostic concern(s).",
        ""
    ]
    
    if sorted_issues:
        for idx, item in enumerate(sorted_issues):
            lines.extend([
                f"{idx+1}. [!] {item['issue']}",
                f"   Confidence Level: {item['confidence']:.1f}%",
                f"   Likely Cause    : {item['cause']}",
                f"   Action Item     : {item['recommendation']}",
                ""
            ])
    else:
        lines.append("[✓] No issues detected! All core indicators are within normal parameters.")
        lines.append("")
        
    lines.append("=================================================================")
    return "\n".join(lines)


# Storage Analyzer
def analyze_storage(parameters: dict = None, player=None) -> str:
    home = Path.home()
    folders = {
        "Desktop": home / "Desktop",
        "Downloads": home / "Downloads",
        "Documents": home / "Documents",
        "Videos": home / "Videos",
        "Pictures": home / "Pictures"
    }
    
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
        folders["Desktop"] = Path(winreg.QueryValueEx(key, "Desktop")[0])
        folders["Documents"] = Path(winreg.QueryValueEx(key, "Personal")[0])
        folders["Pictures"] = Path(winreg.QueryValueEx(key, "My Pictures")[0])
        folders["Videos"] = Path(winreg.QueryValueEx(key, "My Video")[0])
        try:
            folders["Downloads"] = Path(winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")[0])
        except Exception:
            pass
    except Exception:
        pass
        
    folders = {k: v for k, v in folders.items() if v.exists()}
    
    folder_sizes = {}
    all_files = []
    exclusions = {'node_modules', '.git', '.venv', '__pycache__', 'venv', 'env'}
    
    for name, path in folders.items():
        size_accum = 0
        file_count = 0
        try:
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d.lower() not in exclusions and not d.startswith('.')]
                for f in files:
                    file_count += 1
                    if file_count > 5000:
                        break
                    file_path = Path(root) / f
                    try:
                        sz = file_path.stat().st_size
                        size_accum += sz
                        all_files.append((file_path, sz))
                    except Exception:
                        pass
                if file_count > 5000:
                    break
        except Exception:
            pass
        folder_sizes[name] = size_accum

    drive_total_bytes = 1
    if psutil:
        try:
            usage = psutil.disk_usage('C:\\')
            drive_total_bytes = usage.total
        except Exception:
            pass
            
    sorted_folders = sorted(folder_sizes.items(), key=lambda x: x[1], reverse=True)
    sorted_files = sorted(all_files, key=lambda x: x[1], reverse=True)
    
    lines = [
        "============================================================",
        "                 NEXUS AI STORAGE ANALYZER                  ",
        "============================================================",
        "FOLDER SIZE ANALYSIS",
        f"{'Folder':<15} {'Size':<12} {'% of Disk (C:)'}  {'% of Scanned Total'}",
        "-" * 58
    ]
    
    scanned_total_bytes = sum(folder_sizes.values())
    
    for name, sz in sorted_folders:
        sz_gb = sz / (1024**3)
        sz_mb = sz / (1024**2)
        if sz_gb >= 1.0:
            sz_str = f"{sz_gb:.1f} GB"
        else:
            sz_str = f"{sz_mb:.1f} MB"
            
        disk_pct = (sz / drive_total_bytes) * 100
        scanned_pct = (sz / scanned_total_bytes * 100) if scanned_total_bytes > 0 else 0.0
        lines.append(f"{name:<15} {sz_str:<12} {disk_pct:>5.1f}%          {scanned_pct:>5.1f}%")
        
    lines.extend([
        "-" * 58,
        "",
        "LARGEST FILES DETECTED",
        "-" * 58
    ])
    
    for idx, (path, sz) in enumerate(sorted_files[:5]):
        sz_gb = sz / (1024**3)
        sz_mb = sz / (1024**2)
        if sz_gb >= 1.0:
            sz_str = f"{sz_gb:.2f} GB"
        else:
            sz_str = f"{sz_mb:.1f} MB"
            
        path_str = str(path)
        if len(path_str) > 42:
            path_str = "..." + path_str[-39:]
            
        lines.append(f"{idx+1}. {path.name} ({sz_str})")
        lines.append(f"   Path: {path_str}")
        
    if not sorted_files:
        lines.append("No files detected in user folders.")
        
    lines.append("============================================================")
    return "\n".join(lines)


# =================================================================
# FUTURE SYSTEM AWARENESS EXTENSIONS (STUBS & HOOKS)
# =================================================================

def run_network_diagnostics() -> str:
    """Stub for future Network Diagnostics tool."""
    return "Network diagnostics checker is not yet implemented. Future extension stub."

def analyze_wifi_signals() -> str:
    """Stub for future Wi-Fi Analyzer tool."""
    return "Wi-Fi signal analyzer is not yet implemented. Future extension stub."

def get_installed_software() -> str:
    """Stub for future Installed Software Inventory tool."""
    return "Installed software inventory scanner is not yet implemented. Future extension stub."

def check_drivers() -> str:
    """Stub for future Driver Checker tool."""
    return "System driver integrity checker is not yet implemented. Future extension stub."

def check_windows_update_status() -> str:
    """Stub for future Windows Update status checker."""
    return "Windows update compliance checker is not yet implemented. Future extension stub."

def run_security_scan() -> str:
    """Stub for future local security status scanner."""
    return "Security scanner is not yet implemented. Future extension stub."

