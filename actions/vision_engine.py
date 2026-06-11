# actions/vision_engine.py
import os
import re
import sys
import time
import socket
import threading
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

import cv2
import numpy as np
import mss
import mss.tools
import psutil
from PIL import Image

try:
    import win32gui
    import win32process
    import win32con
    import win32com.client
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False

# Local OCR imports
try:
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage import StorageFile, FileAccessMode
    _HAS_WINSDK_OCR = True
except ImportError:
    _HAS_WINSDK_OCR = False

try:
    import easyocr
    _HAS_EASYOCR = True
except ImportError:
    _HAS_EASYOCR = False

try:
    import pytesseract
    _HAS_PYTESSERACT = True
except ImportError:
    _HAS_PYTESSERACT = False

# Global state for Vision diagnostics
_ocr_status = "EasyOCR/Tesseract/Windows Native NOT installed"
if _HAS_WINSDK_OCR:
    _ocr_status = "Windows Native OCR Available"
elif _HAS_EASYOCR:
    _ocr_status = "EasyOCR (Local) Available"
elif _HAS_PYTESSERACT:
    _ocr_status = "PyTesseract (Local) Available"

_last_analysis_time = "--"
_processing_duration = "--"
_screenshot_cache = None  # Caches the last captured PIL Image
_consent_callback = None  # Callback to request user consent for cloud analysis

# Lock for vision operations
_vision_lock = threading.Lock()

try:
    from PyQt6.QtGui import QImage
    _HAS_PYQT_GUI = True
except ImportError:
    _HAS_PYQT_GUI = False

# Global metrics and telemetry variables
_capture_latency = 0.0
_change_detection_latency = 0.0
_ocr_latency = 0.0
_cpu_usage = 0.0
_ram_usage = 0.0
_last_resolution = "1920x1080"
_last_change_blocks = 0
_fps = 0.5

# New preview render, buffering, and telemetry globals
_capture_fps = 0.0
_render_fps = 0.0
_render_latency = 0.0
_frame_drops = 0
_frame_id = 0
_frame_read = True
_latest_frame = None
_display_frame_sidebar = None
_display_frame_center = None
_target_sidebar_w = 132
_target_sidebar_h = 154
_target_center_w = 240
_target_center_h = 180
_center_visible = False

# Memory timeline logs and recovery logs
_activity_timeline = []
_recovery_logs = []
_vision_logs = []

# Context memory store
_context_memory = {
    "active_window": "",
    "detected_apps": [],
    "visible_text": "",
    "project_context": "",
    "current_project": "NEXUS AI",
    "current_file": "None",
    "current_task": "None",
    "current_application": "None"
}

# Configured Blacklist
_blacklist_apps = ["keepass", "1password", "bitwarden", "bank", "credentials", "api_keys.json", "private_keys"]


def set_consent_callback(cb):
    """Sets the callback function to request user consent for cloud uploads."""
    global _consent_callback
    _consent_callback = cb


def get_diagnostics() -> Dict[str, Any]:
    """Returns vision diagnostic information."""
    cache_size = "0 MB"
    if _screenshot_cache is not None:
        try:
            # Estimate PIL image size in memory: width * height * 3 (RGB)
            bytes_sz = _screenshot_cache.width * _screenshot_cache.height * 3
            cache_size = f"{bytes_sz / (1024 * 1024):.2f} MB"
        except Exception:
            cache_size = "Unknown"
            
    return {
        "ocr_status": _ocr_status,
        "last_analysis_time": _last_analysis_time,
        "screenshot_cache_size": cache_size,
        "processing_duration": _processing_duration,
        "capture_latency_ms": round(_capture_latency * 1000, 1),
        "ocr_latency_ms": round(_ocr_latency * 1000, 1),
        "change_detection_ms": round(_change_detection_latency * 1000, 1),
        "cpu_usage_pct": _cpu_usage,
        "ram_usage_mb": round(_ram_usage, 1),
        "resolution": _last_resolution,
        "changes_detected": _last_change_blocks,
        "fps": _fps,
        "capture_fps": round(_capture_fps, 1),
        "render_fps": round(_render_fps, 1),
        "render_latency_ms": round(_render_latency * 1000, 1),
        "frame_drops": _frame_drops
    }


def is_online() -> bool:
    """Checks if the system has an active internet connection."""
    try:
        socket.setdefaulttimeout(1.5)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except Exception:
        return False


def get_current_window_info() -> Dict[str, Any]:
    """Queries foreground window details locally using Windows APIs."""
    info = {
        "hwnd": 0,
        "title": "Unknown",
        "process_name": "Unknown",
        "class_name": "Unknown",
        "app_type": "Unknown",
        "special_info": {}
    }
    
    if not _HAS_WIN32:
        return info
    
    try:
        hwnd = win32gui.GetForegroundWindow()
        if hwnd == 0:
            return info
            
        info["hwnd"] = hwnd
        info["title"] = win32gui.GetWindowText(hwnd)
        info["class_name"] = win32gui.GetClassName(hwnd)
        
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid > 0:
            proc = psutil.Process(pid)
            info["process_name"] = proc.name()
            
        # Analyze and extract contextual insights
        title_lower = info["title"].lower()
        proc_lower = info["process_name"].lower()
        
        # 1. VS Code Analysis
        if "code" in proc_lower or "visual studio code" in title_lower:
            info["app_type"] = "Visual Studio Code"
            parts = [p.strip() for p in info["title"].split(" - ")]
            if len(parts) >= 3:
                open_f = parts[0].lstrip("● ")
                info["special_info"] = {
                    "open_file": open_f,
                    "project": parts[1]
                }
            elif len(parts) == 2:
                info["special_info"] = {
                    "project": parts[0]
                }
                
        # 2. File Explorer Analysis
        elif "explorer" in proc_lower and "cabinetwclass" in info["class_name"].lower():
            info["app_type"] = "File Explorer"
            explorer_path = _get_explorer_path(hwnd)
            if explorer_path:
                info["special_info"] = {"location": explorer_path}
            else:
                info["special_info"] = {"location": info["title"]}
                
        # 3. Browser Analysis
        elif any(b in proc_lower for b in ("chrome", "msedge", "firefox", "opera", "brave")):
            info["app_type"] = "Web Browser"
            domain = "Unknown Website"
            for site in ("github", "google", "youtube", "stackoverflow", "settings", "localhost"):
                if site in title_lower:
                    domain = f"{site}.com" if site != "localhost" else "localhost"
                    break
            info["special_info"] = {
                "active_tab": info["title"],
                "inferred_domain": domain
            }
            
        # 4. Settings Analysis
        elif "applicationframehost" in proc_lower and "settings" in title_lower:
            info["app_type"] = "Windows Settings"
            
        # 5. Terminal/CMD Analysis
        elif any(t in proc_lower for t in ("cmd", "powershell", "wt", "conhost")):
            info["app_type"] = "Command Terminal"
            
    except Exception as e:
        print(f"[VisionEngine] ⚠️ Failed to query active window info: {e}")
        
    return info


def _get_explorer_path(hwnd: int) -> Optional[str]:
    """Helper using Shell COM object to retrieve File Explorer path."""
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        for window in shell.Windows():
            if int(window.hwnd) == hwnd:
                url = window.LocationURL
                if url.startswith("file:///"):
                    raw_path = url[8:]
                    parsed = urllib.parse.unquote(raw_path)
                    return parsed.replace("/", "\\")
    except Exception:
        pass
    return None


def capture_screenshot() -> Image.Image:
    """Captures selected monitor screenshot and caches the PIL image."""
    global _screenshot_cache, _capture_latency, _selected_monitor_idx
    state_mgr = VisionStateManager.get_instance()
    if not state_mgr.is_sharing_active():
        img = Image.new("RGB", (800, 600), (0, 0, 0))
        _screenshot_cache = img
        return img
    t0 = time.perf_counter()
    with mss.mss() as sct:
        idx = _selected_monitor_idx
        if idx < 0 or idx >= len(sct.monitors):
            idx = 0
            
        if len(sct.monitors) == 0:
            img = Image.new("RGB", (800, 600), (0, 0, 0))
            _screenshot_cache = img
            _capture_latency = time.perf_counter() - t0
            return img
            
        monitor = sct.monitors[idx]
        shot = sct.grab(monitor)
        img = Image.frombytes("RGB", shot.size, shot.rgb, "raw", "RGB")
        _screenshot_cache = img
        _capture_latency = time.perf_counter() - t0
        return img


def run_local_ocr(img: Image.Image) -> str:
    """Attempts to run local OCR engines (Winsdk, EasyOCR or PyTesseract)."""
    global _ocr_latency
    t0 = time.perf_counter()
    text = ""
    
    if _HAS_WINSDK_OCR:
        try:
            import asyncio
            async def _async_ocr():
                temp_dir = Path(__file__).resolve().parent.parent / "scratch"
                temp_dir.mkdir(exist_ok=True)
                temp_path = temp_dir / f"temp_ocr_{threading.get_ident()}.png"
                img.save(temp_path, format="PNG")
                try:
                    file = await StorageFile.get_file_from_path_async(str(temp_path.resolve()))
                    stream = await file.open_async(FileAccessMode.READ)
                    decoder = await BitmapDecoder.create_async(stream)
                    software_bitmap = await decoder.get_software_bitmap_async()
                    engine = OcrEngine.try_create_from_user_profile_languages()
                    if engine:
                        result = await engine.recognize_async(software_bitmap)
                        return result.text
                    return ""
                finally:
                    if temp_path.exists():
                        try:
                            temp_path.unlink()
                        except Exception:
                            pass
            
            try:
                text = asyncio.run(_async_ocr())
            except RuntimeError:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    text = executor.submit(asyncio.run, _async_ocr()).result()
        except Exception as e:
            print(f"[VisionEngine] Windows Native OCR error: {e}")

    if not text and _HAS_EASYOCR:
        try:
            img_np = np.array(img)
            reader = easyocr.Reader(['en'], gpu=False)
            results = reader.readtext(img_np)
            lines = [res[1] for res in results]
            text = "\n".join(lines)
        except Exception as e:
            print(f"[VisionEngine] EasyOCR error: {e}")
            
    if not text and _HAS_PYTESSERACT:
        try:
            text = pytesseract.image_to_string(img)
        except Exception as e:
            print(f"[VisionEngine] PyTesseract error: {e}")
            
    _ocr_latency = time.perf_counter() - t0
    return text


def detect_ui_elements_local(text: str) -> List[Dict[str, Any]]:
    """Identifies common UI element texts (buttons, inputs) in the extracted text."""
    elements = []
    lines = text.splitlines()
    button_patterns = [r"\blogin\b", r"\bsubmit\b", r"\bsave\b", r"\bcancel\b", r"\bnext\b", r"\bok\b", r"\bapply\b", r"\bclose\b"]
    input_patterns = [r"\bsearch\b", r"\bemail\b", r"\busername\b", r"\bpassword\b", r"\bphone\b"]
    
    for idx, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean:
            continue
            
        for pat in button_patterns:
            if re.search(pat, line_clean, re.IGNORECASE):
                elements.append({
                    "type": "Button",
                    "label": line_clean[:30],
                    "context": f"Line {idx+1}: {line_clean}"
                })
                break
                
        for pat in input_patterns:
            if re.search(pat, line_clean, re.IGNORECASE):
                elements.append({
                    "type": "Input Field",
                    "label": line_clean[:30],
                    "context": f"Line {idx+1}: {line_clean}"
                })
                break
                
    return elements


def explain_error_text(text: str) -> Optional[Dict[str, str]]:
    """Inspects text for common tracebacks, errors, and warnings."""
    tb_match = re.search(r"(traceback|exception|error|failed|warning)\b", text, re.IGNORECASE)
    if not tb_match:
        return None
        
    report = {
        "error_type": "Unknown Error",
        "extracted_text": "",
        "cause": "Unknown",
        "fix": "N/A"
    }
    
    lines = text.splitlines()
    traceback_lines = []
    capturing_tb = False
    
    for line in lines:
        if "traceback" in line.lower() or "most recent call last" in line.lower():
            capturing_tb = True
        if capturing_tb:
            traceback_lines.append(line)
            if len(traceback_lines) > 12:
                break
                
    if traceback_lines:
        report["extracted_text"] = "\n".join(traceback_lines)
        for l in reversed(traceback_lines):
            if ":" in l and any(err in l for err in ("Error", "Exception", "Fail", "Warn")):
                report["error_type"] = l.split(":")[0].strip()
                break
    else:
        for l in lines:
            if ":" in l and any(err in l for err in ("Error", "Exception", "Failed", "Warning")):
                report["error_type"] = l.split(":")[0].strip()
                report["extracted_text"] = l
                break
                
    et_lower = report["extracted_text"].lower()
    if "modulenotfounderror" in et_lower or "no module named" in et_lower:
        report["error_type"] = "ModuleNotFoundError"
        pkg_match = re.search(r"no module named ['\"]?([a-zA-Z0-9_\-]+)['\"]?", et_lower)
        pkg_name = pkg_match.group(1) if pkg_match else "package_name"
        report["cause"] = f"The required python library '{pkg_name}' is not installed in the active environment."
        report["fix"] = f"pip install {pkg_name}"
    elif "filenotfounderror" in et_lower:
        report["error_type"] = "FileNotFoundError"
        report["cause"] = "The code is attempting to open or modify a file path that does not physically exist."
        report["fix"] = "Verify the target file path and check for spelling or directory hierarchy errors."
    elif "syntaxerror" in et_lower:
        report["error_type"] = "SyntaxError"
        report["cause"] = "Invalid Python code structure or mismatched parentheses/quotes."
        report["fix"] = "Review the highlighted code line for typos or structural errors."
    elif "indentationerror" in et_lower:
        report["error_type"] = "IndentationError"
        report["cause"] = "Inconsistent indentation spacing (spaces vs tabs)."
        report["fix"] = "Ensure consistent indentation (e.g. 4 spaces) across all block segments."
        
    return report


def request_cloud_analysis_with_consent(img: Image.Image, query: str) -> Optional[str]:
    """Helper that invokes OpenRouter client ONLY after obtaining explicit user consent."""
    global _consent_callback
    if not is_online():
        return "ERROR: System is offline. Cannot proceed with cloud fallback analysis."
        
    if not _consent_callback:
        return "ERROR: Consent callback not configured. Cannot request user permission."
        
    has_consent = _consent_callback()
    if not has_consent:
        return "USER_CANCELLED: Advanced cloud analysis declined by the user."
        
    try:
        from or_client import client
        import io
        import base64
        
        img_temp = img.copy()
        img_temp.thumbnail((1024, 768), Image.Resampling.BILINEAR)
        
        buf = io.BytesIO()
        img_temp.save(buf, format="JPEG", quality=75)
        b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
        
        system_instructions = (
            "You are the Vision Intelligence Engine of NEXUS AI, a Personal AI OS developed by Ayushh. "
            "Analyze the screenshot precisely. Extract error messages, list open programs/workspaces, "
            "locate buttons and form fields, and output clear, bulleted answers."
        )
        
        result = client.vision(
            prompt=query,
            image_b64=b64_str,
            mime="image/jpeg",
            system=system_instructions
        )
        return result
    except Exception as e:
        return f"ERROR: Cloud fallback analysis failed. {e}"


def analyze_screen(query: str = "Analyze screen") -> Dict[str, Any]:
    """Executes a full screen analysis run, complying with the local-first hierarchy."""
    global _last_analysis_time, _processing_duration
    t_start = time.perf_counter()
    
    if vision_analysis.is_paused():
        return {
            "timestamp": time.strftime("%H:%M:%S"),
            "duration": "0.00s",
            "active_window": {
                "hwnd": 0,
                "title": "SCREEN SHARING PAUSED",
                "process_name": "Unknown",
                "class_name": "Unknown",
                "app_type": "Unknown",
                "special_info": {}
            },
            "ocr_available": False,
            "ocr_text": "",
            "error": None,
            "ui_elements": [],
            "raw_image": None
        }

    img = capture_screenshot()
    win_info = get_current_window_info()
    
    ocr_text = ""
    ocr_available = _HAS_EASYOCR or _HAS_PYTESSERACT
    
    if ocr_available:
        ocr_text = run_local_ocr(img)
        
    err_report = None
    if ocr_text:
        err_report = explain_error_text(ocr_text)
        
    ui_elements = []
    if ocr_text:
        ui_elements = detect_ui_elements_local(ocr_text)
        
    duration = time.perf_counter() - t_start
    _processing_duration = f"{duration:.2f}s"
    _last_analysis_time = time.strftime("%H:%M:%S")
    
    return {
        "timestamp": _last_analysis_time,
        "duration": _processing_duration,
        "active_window": win_info,
        "ocr_available": ocr_available,
        "ocr_text": ocr_text,
        "error": err_report,
        "ui_elements": ui_elements,
        "raw_image": img
    }


def explain_error_on_screen() -> str:
    """Helper to detect and explain active screen errors."""
    res = analyze_screen()
    if res["error"]:
        report = res["error"]
        return (
            f"Detected Error: {report['error_type']}\n"
            f"Cause: {report['cause']}\n"
            f"Recommended Fix: {report['fix']}\n\n"
            f"Traceback segment:\n{report['extracted_text']}"
        )
        
    if not res["ocr_available"]:
        return (
            "Local OCR engines are unavailable. We can only report active window details:\n"
            f"Active Window: {res['active_window']['title']}\n"
            f"Process: {res['active_window']['process_name']}\n"
            "Please go online to use advanced cloud analysis or install EasyOCR/PyTesseract."
        )
        
    return "No clear error or traceback detected in the local OCR screen capture."


def describe_current_window() -> str:
    """Returns local window analysis summary."""
    win = get_current_window_info()
    desc = f"Current Window: {win['title']}\nProcess Name: {win['process_name']}\nApp Category: {win['app_type']}\n"
    if win["special_info"]:
        for k, v in win["special_info"].items():
            desc += f"{k.replace('_', ' ').capitalize()}: {v}\n"
    return desc


def extract_screen_text() -> str:
    """Returns raw text from screenshot."""
    res = analyze_screen()
    if not res["ocr_available"]:
        return "OCR is unavailable locally. Active Window Title: " + res["active_window"]["title"]
    return res["ocr_text"] if res["ocr_text"].strip() else "No text extracted from screen capture."


def detect_ui_elements() -> str:
    """Returns coordinates of buttons/menus/inputs."""
    res = analyze_screen()
    output = f"Active Window: {res['active_window']['title']}\n\n"
    if not res["ocr_available"]:
        return output + "OCR is unavailable locally. Detailed coordinate detection is disabled."
        
    if not res["ui_elements"]:
        return output + "No common UI element labels (e.g. login, search, submit) detected in local OCR text."
        
    for el in res["ui_elements"]:
        output += f"- {el['label']} ({el['type']}) -> Context: {el['context']}\n"
    return output


def vision_assistant(query: str) -> str:
    """Master router dispatch function."""
    query_lower = query.lower().strip()
    
    if "describe current window" in query_lower or "window analysis" in query_lower:
        return describe_current_window()
        
    res = analyze_screen()
    local_report = (
        f"Active Window: {res['active_window']['title']}\n"
        f"Process: {res['active_window']['process_name']}\n"
        f"App Type: {res['active_window']['app_type']}\n"
    )
    if res['active_window']['special_info']:
        for k, v in res['active_window']['special_info'].items():
            local_report += f"{k.replace('_', ' ').capitalize()}: {v}\n"
            
    if "explain this error" in query_lower:
        if res["error"]:
            report = res["error"]
            return (
                f"Detected Error: {report['error_type']}\n"
                f"Cause: {report['cause']}\n"
                f"Recommended Fix: {report['fix']}\n\n"
                f"Traceback segment:\n{report['extracted_text']}"
            )
        elif not res["ocr_available"]:
            cloud_res = request_cloud_analysis_with_consent(
                res["raw_image"],
                "Look at the screen and explain any active error, traceback, or warning. Suggest a fix."
            )
            if cloud_res:
                return cloud_res
            return local_report + "\nLocal OCR is unavailable and cloud upload was cancelled. No error detected."
        else:
            return local_report + "\nNo traceback or error detected in local screen capture."
            
    if "read screen text" in query_lower or "read this page" in query_lower or "extract text" in query_lower:
        if res["ocr_available"]:
            return res["ocr_text"] if res["ocr_text"].strip() else "Screen appears empty or no text was detected."
        else:
            cloud_res = request_cloud_analysis_with_consent(
                res["raw_image"],
                "Perform OCR on this image. Extract and list all readable text, maintaining layout where possible."
            )
            if cloud_res:
                return cloud_res
            return local_report + "\nLocal OCR is unavailable and cloud upload was cancelled."
            
    if "detect ui elements" in query_lower or "find buttons" in query_lower:
        if res["ocr_available"] and res["ui_elements"]:
            out = local_report + "\nDetected UI Elements:\n"
            for el in res["ui_elements"]:
                out += f"- {el['label']} ({el['type']}) -> {el['context']}\n"
            return out
        elif not res["ocr_available"]:
            cloud_res = request_cloud_analysis_with_consent(
                res["raw_image"],
                "Locate and list coordinates (x,y) of all visible buttons, text inputs, and dropdowns."
            )
            if cloud_res:
                return cloud_res
            return local_report + "\nLocal OCR is unavailable and cloud upload was cancelled."
        else:
            return local_report + "\nNo standard UI element labels detected locally."
            
    if not res["ocr_available"]:
        cloud_res = request_cloud_analysis_with_consent(
            res["raw_image"],
            "Analyze this screen image. What application is open and what is happening? List main sections."
        )
        if cloud_res:
            return cloud_res
        return (
            "LOCAL VISION REPORT:\n"
            f"{local_report}\n"
            "Note: Local OCR dependencies are missing. Cloud analysis was not authorized."
        )
        
    summary = f"LOCAL VISION REPORT:\n{local_report}\n"
    if res["error"]:
        summary += f"\n[WARNING] Detected Error: {res['error']['error_type']}\n"
    if res["ui_elements"]:
        summary += f"\nDetected {len(res['ui_elements'])} UI elements on screen.\n"
    if res["ocr_text"]:
        words = len(res["ocr_text"].split())
        summary += f"\nExtracted {words} words from the screen.\nPreview:\n"
        preview = res["ocr_text"][:300]
        summary += f"{preview}..." if len(res["ocr_text"]) > 300 else preview
        
    return summary


def crop_and_scale_qimage(qimg: QImage, w_lbl: int, h_lbl: int) -> Optional[QImage]:
    if not _HAS_PYQT_GUI or qimg.isNull() or w_lbl <= 0 or h_lbl <= 0:
        return None
    try:
        from PyQt6.QtCore import Qt
        scaled = qimg.scaled(w_lbl, h_lbl, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return scaled
    except Exception:
        return None

_selected_monitor_idx = 1
_ocr_worker = None

def set_selected_monitor(idx: int):
    global _selected_monitor_idx
    _selected_monitor_idx = idx
    log_recovery_event(f"Switched capture monitor source to index {idx}.")

def get_selected_monitor() -> int:
    global _selected_monitor_idx
    return _selected_monitor_idx

def get_monitors_count() -> int:
    try:
        with mss.mss() as sct:
            return len(sct.monitors) - 1
    except Exception:
        return 1

UI_CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "nexus_ui_config.json"

def _load_ui_config() -> dict:
    if UI_CONFIG_FILE.exists():
        try:
            import json
            with open(UI_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "collapsed": False,
        "visible": True,
        "expanded": False,
        "sharing_active": True,
        "accessibility_preset": "NORMAL"
    }

def _save_ui_config_state(sharing_active: bool, accessibility_preset: str):
    try:
        import json
        config = _load_ui_config()
        config["sharing_active"] = sharing_active
        config["accessibility_preset"] = accessibility_preset
        UI_CONFIG_FILE.parent.mkdir(exist_ok=True)
        with open(UI_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f)
    except Exception as e:
        print(f"[VisionEngine] Failed to save config: {e}")

class VisionStateManager:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._lock = threading.Lock()
        self._sharing_active = True
        self._accessibility_preset = "NORMAL"
        self._subscribers = []

        # Load persisted settings
        config = _load_ui_config()
        self._sharing_active = config.get("sharing_active", True)
        self._accessibility_preset = config.get("accessibility_preset", "NORMAL")

    def is_sharing_active(self) -> bool:
        with self._lock:
            return self._sharing_active

    def set_sharing_active(self, active: bool):
        with self._lock:
            if self._sharing_active == active:
                return
            self._sharing_active = active
            preset = self._accessibility_preset
        
        # Save config
        _save_ui_config_state(active, preset)

        # Control pipeline components
        if active:
            capture_thread.resume()
            ocr_worker.resume()
            vision_analysis.resume()
        else:
            capture_thread.pause()
            ocr_worker.pause()
            vision_analysis.pause()

        self.notify_subscribers()

    def get_accessibility_preset(self) -> str:
        with self._lock:
            return self._accessibility_preset

    def set_accessibility_preset(self, preset: str):
        with self._lock:
            if preset not in ("NORMAL", "LARGE", "EXTRA LARGE"):
                return
            if self._accessibility_preset == preset:
                return
            self._accessibility_preset = preset
            active = self._sharing_active

        # Save config
        _save_ui_config_state(active, preset)

        self.notify_subscribers()

    def get_vision_status(self) -> str:
        if not self.is_sharing_active():
            return "PAUSED"
        service = get_vision_service()
        if not service._running:
            return "IDLE"
        return "ACTIVE"

    def get_ocr_status(self) -> str:
        if not self.is_sharing_active():
            return "DISABLED"
        global _ocr_worker
        if _ocr_worker is not None and _ocr_worker.is_busy:
            return "ACTIVE"
        return "IDLE"

    def register_subscriber(self, callback):
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unregister_subscriber(self, callback):
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def notify_subscribers(self):
        with self._lock:
            subs = list(self._subscribers)
        for sub in subs:
            try:
                sub()
            except Exception as e:
                print(f"[VisionStateManager] Error notifying subscriber: {e}")

class CaptureThreadProxy:
    def pause(self):
        get_vision_service().pause()
    def resume(self):
        get_vision_service().resume()
    def is_paused(self):
        return get_vision_service().is_paused()

class OCRWorkerProxy:
    def pause(self):
        global _ocr_worker
        if _ocr_worker is not None:
            _ocr_worker.pause()
    def resume(self):
        global _ocr_worker
        if _ocr_worker is not None:
            _ocr_worker.resume()

class VisionAnalysisControl:
    def __init__(self):
        self._paused = False
    def pause(self):
        self._paused = True
    def resume(self):
        self._paused = False
    def is_paused(self):
        return self._paused

capture_thread = CaptureThreadProxy()
ocr_worker = OCRWorkerProxy()
vision_analysis = VisionAnalysisControl()

class OCRWorker(threading.Thread):
    def __init__(self):
        super().__init__(name="NEXUS_VisionOCR", daemon=True)
        self.cond = threading.Condition()
        self.pending_image = None
        self.pending_win_info = None
        self.pending_changed_indices = None
        self._running = True
        self.is_busy = False
        
        # Check initial sharing state
        state_mgr = VisionStateManager.get_instance()
        self._paused = not state_mgr.is_sharing_active()
        
    def pause(self):
        with self.cond:
            self._paused = True
            self.pending_image = None
            self.pending_win_info = None
            self.pending_changed_indices = None
            self.cond.notify_all()
            
    def resume(self):
        with self.cond:
            self._paused = False
            self.cond.notify_all()

    def stop(self):
        with self.cond:
            self._running = False
            self.cond.notify_all()
        
    def run(self):
        global _last_analysis_time, _context_memory
        while self._running:
            with self.cond:
                while self.pending_image is None and self._running:
                    self.cond.wait()
                if not self._running:
                    break
                if self._paused:
                    self.pending_image = None
                    self.pending_win_info = None
                    self.pending_changed_indices = None
                    continue
                img = self.pending_image
                win_info = self.pending_win_info
                changed_indices = self.pending_changed_indices
                self.pending_image = None
                self.pending_win_info = None
                self.pending_changed_indices = None
                self.is_busy = True
                
            try:
                if self._paused:
                    continue
                # Region cropping & OCR
                W, H = img.size
                min_x, min_y, max_x, max_y = W, H, 0, 0
                for idx in changed_indices:
                    r = idx // 8
                    c = idx % 8
                    bx1 = int((c / 8) * W)
                    by1 = int((r / 8) * H)
                    bx2 = int(((c + 1) / 8) * W)
                    by2 = int(((r + 1) / 8) * H)
                    
                    min_x = min(min_x, bx1)
                    min_y = min(min_y, by1)
                    max_x = max(max_x, bx2)
                    max_y = max(max_y, by2)
                    
                ocr_text = ""
                if max_x > min_x and max_y > min_y:
                    cropped = img.crop((min_x, min_y, max_x, max_y))
                    if self._paused:
                        continue
                    ocr_text = run_local_ocr(cropped)
                    
                if self._paused:
                    continue
                # Extract Context & Activities
                activity_type, details = classify_activity(win_info)
                update_memory_context(win_info, ocr_text, activity_type, details)
                record_timeline_event(win_info["process_name"], activity_type, details)
                
                # Deep Developer Awareness & AST analysis
                if win_info["app_type"] == "Visual Studio Code":
                    dev_insights = extract_developer_insights(win_info)
                    _context_memory["project_context"] = str(dev_insights)
                    
            except Exception as e:
                print(f"[VisionOCRWorker] Error in background OCR: {e}")
            finally:
                self.is_busy = False

    def trigger_ocr(self, img, win_info, changed_indices) -> bool:
        with self.cond:
            if self.is_busy or self._paused:
                return False
            self.pending_image = img
            self.pending_win_info = win_info
            self.pending_changed_indices = changed_indices
            self.cond.notify()
            return True

def set_target_sidebar_size(w: int, h: int):
    global _target_sidebar_w, _target_sidebar_h
    _target_sidebar_w = max(1, w)
    _target_sidebar_h = max(1, h)

def set_target_center_size(w: int, h: int):
    global _target_center_w, _target_center_h
    _target_center_w = max(1, w)
    _target_center_h = max(1, h)

def set_center_visible(visible: bool):
    global _center_visible
    _center_visible = visible

# =====================================================================
#             VISION MODE UPGRADES IMPLEMENTATION
# =====================================================================

class ScreenCaptureService:
    """Manages the background periodic screen capture thread."""
    def __init__(self, interval=2.0):
        self.interval = interval
        self.thread = None
        self._running = False
        self._paused = False
        self._stop_event = threading.Event()
        self._last_means = None
        self._prev_frame = None
        self._lock = threading.Lock()
        
    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._paused = False
            self._stop_event.clear()
            
            global _ocr_worker
            if _ocr_worker is None or not _ocr_worker.is_alive():
                _ocr_worker = OCRWorker()
                _ocr_worker.start()
                
            self.thread = threading.Thread(target=self._loop, name="NEXUS_VisionCapture", daemon=True)
            self.thread.start()
            log_recovery_event("ScreenCaptureService started background loop thread.")
            
    def stop(self):
        with self._lock:
            self._running = False
            self._stop_event.set()
            
    def pause(self):
        with self._lock:
            self._paused = True
            log_recovery_event("ScreenCaptureService captures paused.")
            
    def resume(self):
        with self._lock:
            self._paused = False
            log_recovery_event("ScreenCaptureService captures resumed.")
            
    def is_paused(self):
        with self._lock:
            return self._paused
            
    def is_alive(self):
        return self.thread is not None and self.thread.is_alive()
        
    def _loop(self):
        global _last_analysis_time, _processing_duration, _cpu_usage, _ram_usage
        global _capture_fps, _render_latency, _frame_drops, _frame_id, _frame_read
        global _latest_frame, _display_frame_sidebar, _display_frame_center
        global _last_resolution, _last_change_blocks, _capture_latency, _change_detection_latency
        import math
        
        last_ocr_time = 0.0
        current_interval = 0.5  # Start with idle
        
        while self._running:
            self._stop_event.wait(timeout=current_interval)
            if not self._running:
                break
            
            if self.is_paused():
                continue
                
            try:
                t_loop_start = time.perf_counter()
                
                # 1. Active Window & Privacy Check
                win_info = get_current_window_info()
                title = win_info["title"].lower()
                proc = win_info["process_name"].lower()
                
                # Check blacklist
                is_blacklisted = False
                for blacklisted in _blacklist_apps:
                    if blacklisted in title or blacklisted in proc:
                        is_blacklisted = True
                        break
                        
                if is_blacklisted:
                    log_vision_event(time.strftime("%H:%M:%S"), 1, "Hidden", False, "Skipped capture: Blacklisted application active.")
                    current_interval = 0.5
                    continue
                
                # Update System Performance Telemetry
                try:
                    p = psutil.Process(os.getpid())
                    _cpu_usage = p.cpu_percent()
                    _ram_usage = p.memory_info().rss / (1024 * 1024)
                except Exception:
                    pass
                
                # 2. Capture Screenshot
                t_capture_start = time.perf_counter()
                img = capture_screenshot()
                capture_lat = time.perf_counter() - t_capture_start
                _last_resolution = f"{img.width}x{img.height}"
                
                # Compute Capture FPS
                if hasattr(self, '_last_capture_time'):
                    dt = t_loop_start - self._last_capture_time
                    if dt > 0:
                        _capture_fps = 1.0 / dt
                self._last_capture_time = t_loop_start
                
                # 3. Smart Region Change Detection
                t_change_start = time.perf_counter()
                img_gray = img.convert("L").resize((256, 256))
                img_np = np.array(img_gray, dtype=np.int16)
                
                blocks = []
                for r in range(8):
                    for c in range(8):
                        block = img_np[r*32:(r+1)*32, c*32:(c+1)*32]
                        blocks.append(float(np.mean(block)))
                        
                changed = True
                changed_indices = list(range(64))
                
                if self._last_means is not None:
                    diffs = [abs(m1 - m2) for m1, m2 in zip(self._last_means, blocks)]
                    changed_indices = [idx for idx, d in enumerate(diffs) if d > 1.8]
                    total_mean_diff = np.mean(diffs)
                    if len(changed_indices) == 0 or total_mean_diff < 0.6:
                        changed = False
                        
                _last_change_blocks = len(changed_indices) if changed else 0
                self._last_means = blocks
                _change_detection_latency = time.perf_counter() - t_change_start
                
                _last_analysis_time = time.strftime("%H:%M:%S")
                res_str = f"{img.width}x{img.height}"
                log_vision_event(_last_analysis_time, get_selected_monitor(), res_str, changed, f"Frame captured in {round(capture_lat*1000, 1)}ms.")
                
                # Adaptive Refresh Rate adjustment
                if changed:
                    current_interval = 0.1  # 100ms on activity
                else:
                    current_interval = 0.5  # 500ms when idle
                
                # 4. Background Rendering (Double Buffering)
                if changed and _HAS_PYQT_GUI:
                    t_render_start = time.perf_counter()
                    
                    # Convert PIL Image to QImage
                    img_rgb = img.convert("RGB")
                    raw_data = img_rgb.tobytes("raw", "RGB")
                    qimg = QImage(raw_data, img_rgb.width, img_rgb.height, img_rgb.width * 3, QImage.Format.Format_RGB888).copy()
                    
                    # Perform crops and scaling in background thread
                    scaled_sidebar = crop_and_scale_qimage(qimg, _target_sidebar_w, _target_sidebar_h)
                    scaled_center = None
                    if _center_visible:
                        scaled_center = crop_and_scale_qimage(qimg, _target_center_w, _target_center_h)
                        
                    # Update double buffers safely under lock
                    with _vision_lock:
                        _latest_frame = qimg
                        _display_frame_sidebar = scaled_sidebar
                        if scaled_center is not None:
                            _display_frame_center = scaled_center
                        
                        # Monitor frame drops
                        if not _frame_read:
                            _frame_drops += 1
                        _frame_read = False
                        _frame_id += 1
                        
                    _render_latency = time.perf_counter() - t_render_start
                
                # Skip OCR if frame hasn't changed
                if not changed:
                    continue
                    
                # Rate-limit OCR to at most 1 run per 500ms to keep CPU usage low
                now_time = time.perf_counter()
                if now_time - last_ocr_time >= 0.5:
                    if _ocr_worker.trigger_ocr(img, win_info, changed_indices):
                        last_ocr_time = now_time
                        
            except Exception as e:
                print(f"[VisionEngine] Capture service thread error: {e}")
                log_recovery_event(f"ScreenCaptureService loop encountered exception: {e}")


# Singleton Capture Service instance
_capture_service = ScreenCaptureService()


def get_vision_service() -> ScreenCaptureService:
    global _capture_service
    return _capture_service


def start_vision_mode():
    get_vision_service().start()


def stop_vision_mode():
    get_vision_service().stop()
    global _ocr_worker
    if _ocr_worker is not None:
        _ocr_worker.stop()


def pause_vision_mode():
    get_vision_service().pause()


def resume_vision_mode():
    get_vision_service().resume()


def log_vision_event(timestamp, monitor, resolution, changed, description):
    global _vision_logs
    event = {
        "timestamp": timestamp,
        "monitor": monitor,
        "resolution": resolution,
        "changed": changed,
        "description": description
    }
    _vision_logs.append(event)
    if len(_vision_logs) > 100:
        _vision_logs.pop(0)


def log_recovery_event(event_text):
    global _recovery_logs
    log_entry = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {event_text}"
    _recovery_logs.append(log_entry)
    print(f"[VisionEngine Recovery] {event_text}")
    if len(_recovery_logs) > 50:
        _recovery_logs.pop(0)


def get_vision_logs() -> List[Dict[str, Any]]:
    global _vision_logs
    return _vision_logs


def get_recovery_logs() -> List[str]:
    global _recovery_logs
    return _recovery_logs


def get_vision_timeline() -> List[Dict[str, Any]]:
    global _activity_timeline
    return _activity_timeline


def classify_activity(win_info: Dict[str, Any]) -> Tuple[str, str]:
    app_type = win_info["app_type"]
    title = win_info["title"].lower()
    proc = win_info["process_name"].lower()
    
    if app_type == "Visual Studio Code" or "pycharm" in proc or "eclipse" in proc:
        return "Coding", f"Working in codebase: {win_info['special_info'].get('project', 'Unknown Project')}"
    elif app_type == "Command Terminal":
        return "Terminal usage", f"Executing commands in {win_info['process_name']}"
    elif app_type == "Web Browser":
        domain = win_info["special_info"].get("inferred_domain", "Unknown Website")
        if "youtube" in title or "netflix" in title or "twitch" in title:
            return "Watching videos", f"Streaming media on {domain}"
        elif "pdf" in title or "pdf" in proc or "document" in title:
            return "Reading documents", f"Reading PDF/Documentation in browser"
        else:
            return "Browsing", f"Navigating site: {domain}"
    elif "excel" in proc or "powerpnt" in proc or "winword" in proc or "adobe" in proc or "acrobat" in proc:
        return "Reading documents", f"Modifying office document: {win_info['title']}"
    else:
        return "Idle", f"Active program: {win_info['process_name']}"


def update_memory_context(win_info: Dict[str, Any], ocr_text: str, activity_type: str, details: str):
    global _context_memory
    _context_memory["active_window"] = win_info["title"]
    _context_memory["current_application"] = win_info["process_name"]
    _context_memory["visible_text"] = ocr_text
    
    # Extract project names
    if win_info["special_info"] and "project" in win_info["special_info"]:
        _context_memory["current_project"] = win_info["special_info"]["project"]
    if win_info["special_info"] and "open_file" in win_info["special_info"]:
        _context_memory["current_file"] = win_info["special_info"]["open_file"]
        
    _context_memory["current_task"] = f"Activity: {activity_type} - {details}"


def record_timeline_event(app: str, activity: str, details: str):
    global _activity_timeline
    event = {
        "timestamp": time.strftime("%H:%M:%S"),
        "app": app,
        "activity": activity,
        "details": details
    }
    _activity_timeline.append(event)
    if len(_activity_timeline) > 100:
        _activity_timeline.pop(0)


def query_activities(query_str: str) -> List[Dict[str, Any]]:
    global _activity_timeline
    q = query_str.lower()
    return [e for e in _activity_timeline if q in e["app"].lower() or q in e["activity"].lower() or q in e["details"].lower()]


def extract_developer_insights(win_info: Dict[str, Any]) -> Dict[str, Any]:
    project_dir = Path(__file__).resolve().parent.parent
    insights = {
        "open_file": "None",
        "language": "Unknown",
        "imports": [],
        "classes": [],
        "functions": [],
        "errors": [],
        "warnings": [],
        "missing_tests": False,
        "code_smells": [],
        "refactoring_opportunities": [],
        "workspace": str(project_dir),
        "errors_count": 0,
        "warnings_count": 0
    }
    
    open_file = win_info["special_info"].get("open_file")
    if not open_file:
        return insights
        
    insights["open_file"] = open_file
    
    # Infer language
    suffix = Path(open_file).suffix.lower()
    lang_mapping = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".jsx": "React JS", ".tsx": "React TS", ".java": "Java", ".cpp": "C++", ".c": "C", ".html": "HTML", ".css": "CSS"}
    insights["language"] = lang_mapping.get(suffix, "Plain Text")
    
    # Locate open file in project workspaces
    found_path = None
    for r, d, files in os.walk(project_dir):
        if open_file in files:
            found_path = Path(r) / open_file
            break
            
    if found_path and insights["language"] == "Python":
        # AST analysis
        try:
            import ast
            code = found_path.read_text(encoding="utf-8")
            tree = ast.parse(code)
            
            # Syntax/Compilation check
            try:
                compile(code, str(found_path), "exec")
            except SyntaxError as se:
                insights["errors"].append(f"Syntax Error: {se.msg} on line {se.lineno}")
                
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for name in node.names:
                        insights["imports"].append(name.name)
                elif isinstance(node, ast.ClassDef):
                    insights["classes"].append(node.name)
                elif isinstance(node, ast.FunctionDef):
                    insights["functions"].append(node.name)
                    # Simple refactoring opportunity check: function too long
                    loc = len(node.body)
                    if loc > 30:
                        insights["refactoring_opportunities"].append(
                            f"Function '{node.name}' is complex ({loc} statements). Consider decomposing."
                        )
            
            # Code smells & excessive nesting check
            lines = code.splitlines()
            for idx, l in enumerate(lines):
                leading_spaces = len(l) - len(l.lstrip(' '))
                nest = leading_spaces // 4
                if nest > 5:
                    insights["code_smells"].append(f"Excessive Indentation level {nest} at line {idx+1}")
                    insights["refactoring_opportunities"].append(f"Flatten nesting block at line {idx+1}")
            
            # Missing Tests check
            test_name = f"test_{found_path.stem}.py"
            test_dir = found_path.parent / "tests"
            scratch_dir = found_path.parent / "scratch"
            test_exists = False
            
            for path_to_chk in (found_path.parent, test_dir, scratch_dir):
                if path_to_chk.exists():
                    for f in path_to_chk.iterdir():
                        if f.is_file() and (f.name == test_name or f.name == f"test_{found_path.name}"):
                            test_exists = True
                            break
                            
            if not test_exists:
                insights["missing_tests"] = True
                insights["refactoring_opportunities"].append(f"Create test file: {test_name} for validation.")
                
        except Exception as e:
            insights["errors"].append(f"AST Parse failed: {e}")
            
    insights["errors_count"] = len(insights["errors"])
    insights["warnings_count"] = len(insights["warnings"])
    return insights


def get_vision_context() -> Dict[str, Any]:
    global _context_memory
    state_mgr = VisionStateManager.get_instance()
    if not state_mgr.is_sharing_active():
        return {
            "active_window": "SCREEN SHARING PAUSED",
            "detected_apps": [],
            "visible_text": "",
            "project_context": "",
            "current_project": "None",
            "current_file": "None",
            "current_task": "None",
            "current_application": "None"
        }
    return _context_memory

# =====================================================================
#             SELF-HEALING WATCHDOG MANAGER
# =====================================================================

class VisionWatchdog(threading.Thread):
    """Monitors vision components (service thread and preview UI window) and self-heals crashes."""
    def __init__(self, ui_app=None, interval=3.0):
        super().__init__(name="NEXUS_VisionWatchdog", daemon=True)
        self.interval = interval
        self.ui_app = ui_app
        self._running = False
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        
    def start_watchdog(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self.start()
            log_recovery_event("VisionWatchdog daemon started successfully.")
            
    def stop_watchdog(self):
        with self._lock:
            self._running = False
            self._stop_event.set()
            
    def run(self):
        while self._running:
            self._stop_event.wait(timeout=self.interval)
            if not self._running:
                break
            self.check_components()

    def check_components(self):
        # 1. Health check ScreenCaptureService
        service = get_vision_service()
        if service._running and not service.is_alive():
            log_recovery_event("CRASH DETECTED: ScreenCaptureService thread died unexpectedly. Self-healing active...")
            try:
                service._running = False  # Reset running to permit start thread
                service.start()
                log_recovery_event("RECOVERED: ScreenCaptureService thread restarted successfully.")
            except Exception as restart_err:
                log_recovery_event(f"RECOVERY FAILED: ScreenCaptureService restart failed: {restart_err}")
        
        # 2. Health check Preview Widget
        if self.ui_app:
            # Expecting ui_app to be a MainWindow instance or hold a reference
            if hasattr(self.ui_app, "_vision_widget_enabled") and self.ui_app._vision_widget_enabled:
                widget_alive = False
                if hasattr(self.ui_app, "vision_preview_widget") and self.ui_app.vision_preview_widget is not None:
                    try:
                        # Widget exists, check if it was closed or hidden unexpectedly
                        if self.ui_app.vision_preview_widget.isHidden() or not self.ui_app.vision_preview_widget.isVisible():
                            widget_alive = False
                        else:
                            widget_alive = True
                    except Exception:
                        widget_alive = False
                        
                if not widget_alive:
                    log_recovery_event("CRASH DETECTED: VisionPreviewWidget closed unexpectedly. Self-healing active...")
                    # Run widget relaunch on main PyQt UI thread via QTimer/Signal
                    try:
                        self.ui_app.relaunch_preview_widget_signal.emit()
                        log_recovery_event("RECOVERED: VisionPreviewWidget relaunched.")
                    except Exception as relaunch_err:
                        log_recovery_event(f"RECOVERY FAILED: VisionPreviewWidget relaunch failed: {relaunch_err}")


_watchdog_instance = None


def init_vision_watchdog(ui_app=None):
    global _watchdog_instance
    if _watchdog_instance is None:
        _watchdog_instance = VisionWatchdog(ui_app)
        _watchdog_instance.start_watchdog()
    return _watchdog_instance

