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
_ocr_status = "EasyOCR/Tesseract NOT installed"
if _HAS_EASYOCR:
    _ocr_status = "EasyOCR (Local) Available"
elif _HAS_PYTESSERACT:
    _ocr_status = "PyTesseract (Local) Available"

_last_analysis_time = "--"
_processing_duration = "--"
_screenshot_cache = None  # Caches the last captured PIL Image
_consent_callback = None  # Callback to request user consent for cloud analysis

# Lock for vision operations
_vision_lock = threading.Lock()


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
        "processing_duration": _processing_duration
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
            # VS Code title format: "[File Name] - [Project Name] - Visual Studio Code"
            parts = [p.strip() for p in info["title"].split(" - ")]
            if len(parts) >= 3:
                info["special_info"] = {
                    "open_file": parts[0],
                    "project": parts[1]
                }
            elif len(parts) == 2:
                info["special_info"] = {
                    "project": parts[0]
                }
                
        # 2. File Explorer Analysis
        elif "explorer" in proc_lower and "cabinetwclass" in info["class_name"].lower():
            info["app_type"] = "File Explorer"
            # Get path of explorer using COM
            explorer_path = _get_explorer_path(hwnd)
            if explorer_path:
                info["special_info"] = {"location": explorer_path}
            else:
                info["special_info"] = {"location": info["title"]}
                
        # 3. Browser Analysis
        elif any(b in proc_lower for b in ("chrome", "msedge", "firefox", "opera", "brave")):
            info["app_type"] = "Web Browser"
            # Try to identify domain/platform from window title
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
                    # Remove file:/// prefix and parse URL escaping
                    raw_path = url[8:]
                    parsed = urllib.parse.unquote(raw_path)
                    return parsed.replace("/", "\\")
    except Exception:
        pass
    return None


def capture_screenshot() -> Image.Image:
    """Captures primary monitor screenshot and caches the PIL image."""
    global _screenshot_cache
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)
        # Convert to RGB PIL Image
        img = Image.frombytes("RGB", shot.size, shot.rgb, "raw", "RGB")
        _screenshot_cache = img
        return img


def run_local_ocr(img: Image.Image) -> str:
    """Attempts to run local OCR engines (EasyOCR or PyTesseract)."""
    if _HAS_EASYOCR:
        try:
            # EasyOCR requires numpy array or file path
            img_np = np.array(img)
            reader = easyocr.Reader(['en'], gpu=False)
            results = reader.readtext(img_np)
            lines = [res[1] for res in results]
            return "\n".join(lines)
        except Exception as e:
            print(f"[VisionEngine] EasyOCR error: {e}")
            
    if _HAS_PYTESSERACT:
        try:
            # Set path to Tesseract if configured (can check environment or common paths)
            text = pytesseract.image_to_string(img)
            return text
        except Exception as e:
            print(f"[VisionEngine] PyTesseract error: {e}")
            
    return ""


def detect_ui_elements_local(text: str) -> List[Dict[str, Any]]:
    """Identifies common UI element texts (buttons, inputs) in the extracted text."""
    elements = []
    lines = text.splitlines()
    
    # Common UI element label patterns
    button_patterns = [r"\blogin\b", r"\bsubmit\b", r"\bsave\b", r"\bcancel\b", r"\bnext\b", r"\bok\b", r"\bapply\b", r"\bclose\b"]
    input_patterns = [r"\bsearch\b", r"\bemail\b", r"\busername\b", r"\bpassword\b", r"\bphone\b"]
    
    for idx, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean:
            continue
            
        # Detect Buttons
        for pat in button_patterns:
            if re.search(pat, line_clean, re.IGNORECASE):
                elements.append({
                    "type": "Button",
                    "label": line_clean[:30],
                    "context": f"Line {idx+1}: {line_clean}"
                })
                break
                
        # Detect Inputs
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
    # Look for Python tracebacks or common error declarations
    # Key indicators: ModuleNotFoundError, Exception, ValueError, TypeError, Failed, Traceback (most recent call last)
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
            if len(traceback_lines) > 12:  # Cap at 12 lines
                break
                
    if traceback_lines:
        report["extracted_text"] = "\n".join(traceback_lines)
        # Try to identify last line of traceback which has the actual error type
        for l in reversed(traceback_lines):
            if ":" in l and any(err in l for err in ("Error", "Exception", "Fail", "Warn")):
                report["error_type"] = l.split(":")[0].strip()
                break
    else:
        # Scan for lines containing "Error:" or "Exception:"
        for l in lines:
            if ":" in l and any(err in l for err in ("Error", "Exception", "Failed", "Warning")):
                report["error_type"] = l.split(":")[0].strip()
                report["extracted_text"] = l
                break
                
    # Direct cause/fix mapper for common Python issues
    et_lower = report["extracted_text"].lower()
    if "modulenotfounderror" in et_lower or "no module named" in et_lower:
        report["error_type"] = "ModuleNotFoundError"
        # Extract package name
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
        
    # Check if a consent callback has been registered
    if not _consent_callback:
        return "ERROR: Consent callback not configured. Cannot request user permission."
        
    # Trigger the callback. This will display a popup dialog or terminal prompt.
    # The callback must block or await user response, returning True or False.
    has_consent = _consent_callback()
    if not has_consent:
        return "USER_CANCELLED: Advanced cloud analysis declined by the user."
        
    # Perform analysis via OpenRouter Client
    try:
        from or_client import client
        import io
        import base64
        
        # Resize image slightly to optimize payload transfer speed (max 1024 width)
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
    
    # 1. Capture screen
    img = capture_screenshot()
    
    # 2. Local active window detection
    win_info = get_current_window_info()
    
    # 3. OCR Analysis
    ocr_text = ""
    ocr_available = _HAS_EASYOCR or _HAS_PYTESSERACT
    
    if ocr_available:
        ocr_text = run_local_ocr(img)
        
    # 4. Local Error Analysis
    err_report = None
    if ocr_text:
        err_report = explain_error_text(ocr_text)
        
    # 5. Local UI Detection
    ui_elements = []
    if ocr_text:
        ui_elements = detect_ui_elements_local(ocr_text)
        
    # Record processing time
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
    
    # Check if the query is a simple current window description (fully local, no OCR needed)
    if "describe current window" in query_lower or "window analysis" in query_lower:
        return describe_current_window()
        
    # Analyze screen and compile local findings
    res = analyze_screen()
    
    # Prepare local findings report
    local_report = (
        f"Active Window: {res['active_window']['title']}\n"
        f"Process: {res['active_window']['process_name']}\n"
        f"App Type: {res['active_window']['app_type']}\n"
    )
    if res['active_window']['special_info']:
        for k, v in res['active_window']['special_info'].items():
            local_report += f"{k.replace('_', ' ').capitalize()}: {v}\n"
            
    # If the user query is simple or local OCR suffices
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
            # Prompt for cloud fallback
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
            # Prompt for cloud fallback
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
            # Prompt for cloud fallback
            cloud_res = request_cloud_analysis_with_consent(
                res["raw_image"],
                "Locate and list coordinates (x,y) of all visible buttons, text inputs, and dropdowns."
            )
            if cloud_res:
                return cloud_res
            return local_report + "\nLocal OCR is unavailable and cloud upload was cancelled."
        else:
            return local_report + "\nNo standard UI element labels detected locally."
            
    # Default screen analysis response
    if not res["ocr_available"]:
        # Offer cloud fallback
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
        
    # Complete local report including OCR text summary
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
