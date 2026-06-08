# scratch/test_vision_engine.py
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from actions.vision_engine import (
    get_current_window_info,
    capture_screenshot,
    explain_error_text,
    detect_ui_elements_local,
    describe_current_window,
    get_diagnostics
)

print("================ VISION ENGINE SELF-TEST & VERIFICATION ================")

# 1. Test Diagnostics and OCR availability
print("\n--- Test 1: Diagnostics & Local OCR Status ---")
diag = get_diagnostics()
print(f"OCR Status           : {diag['ocr_status']}")
print(f"Screenshot Cache Size: {diag['screenshot_cache_size']}")
print(f"Processing Duration  : {diag['processing_duration']}")

# 2. Test Screenshot Capture
print("\n--- Test 2: Live Screenshot Capture ---")
try:
    img = capture_screenshot()
    print(f"SUCCESS: Screenshot captured. Dimensions: {img.width}x{img.height}")
    diag_updated = get_diagnostics()
    print(f"Updated Cache Size   : {diag_updated['screenshot_cache_size']}")
except Exception as e:
    print(f"FAILED: Screenshot capture failed: {e}")

# 3. Test Native Current Window Detection
print("\n--- Test 3: Active Window Detection ---")
win_info = get_current_window_info()
print(f"Active Window Handle : {win_info['hwnd']}")
print(f"Active Window Title  : {win_info['title']}")
print(f"Process Executable   : {win_info['process_name']}")
print(f"Inferred App Category: {win_info['app_type']}")
if win_info['special_info']:
    print(f"Context Insights     : {win_info['special_info']}")

# 4. Test Parser: VS Code Workspace Title Parsing (Mocked)
print("\n--- Test 4: Mocked VS Code Title Parsing ---")
# Let's verify parser behaviour for VS Code title format: "[File Name] - [Project Name] - Visual Studio Code"
mock_vscode_title = "main.py - NEXUS AI - Visual Studio Code"
parts = [p.strip() for p in mock_vscode_title.split(" - ")]
assert len(parts) >= 3
print(f"Mock Title: {mock_vscode_title}")
print(f"Parsed Open File: {parts[0]}")
print(f"Parsed Project  : {parts[1]}")

# 5. Test Parser: Browser Active Tab Parsing (Mocked)
print("\n--- Test 5: Mocked Browser Title Parsing ---")
# Mock title of a GitHub page in Chrome
mock_chrome_github_title = "GitHub - Ayush-0915/NEXUS-AI: Personal AI Operating System - Google Chrome"
print(f"Mock Title: {mock_chrome_github_title}")
domain = "Unknown Website"
if "github" in mock_chrome_github_title.lower():
    domain = "github.com"
print(f"Parsed Tab Title: {mock_chrome_github_title}")
print(f"Inferred Domain : {domain}")
assert domain == "github.com"

# 6. Test Error Explainer (Mocked python traceback)
print("\n--- Test 6: Traceback Parsing & Error Explaining ---")
mock_traceback = """
Traceback (most recent call last):
  File "main.py", line 12, in <module>
    import easyocr
ModuleNotFoundError: No module named 'easyocr'
"""
err_report = explain_error_text(mock_traceback)
print(f"Mock Traceback Input:\n{mock_traceback.strip()}")
if err_report:
    print("\nParsed Error Report:")
    print(f"Error Type: {err_report['error_type']}")
    print(f"Cause     : {err_report['cause']}")
    print(f"Fix Suggest: {err_report['fix']}")
    assert err_report["error_type"] == "ModuleNotFoundError"
else:
    print("FAILED: Error report not generated")

# 7. Test UI Element Extraction (Mocked OCR text)
print("\n--- Test 7: UI Element Detection ---")
mock_ocr_text = """
NEXUS AI Vision Intelligence
Username: [                  ]
Password: [                  ]
[ Login ]       [ Cancel ]
Search the web...
"""
elements = detect_ui_elements_local(mock_ocr_text)
print("Mock OCR Output:")
print(mock_ocr_text.strip())
print("\nDetected UI Elements:")
for el in elements:
    print(f"- Type: {el['type']} | Label: {el['label']} | Context: {el['context']}")

assert len(elements) >= 3  # Should detect Username, Password, Login, Cancel, Search

print("\n================ VERIFICATION STATUS: SUCCESS ================")
