# actions/system_info.py

import os
import sys
import platform
import socket
import subprocess
import json
import re

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
