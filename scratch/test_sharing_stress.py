import sys
import time
import threading
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# Mock PyQt to run headlessly without real Qt GUI loop
from PyQt6.QtWidgets import QApplication
app = QApplication.instance()
if not app:
    app = QApplication([])

from actions.vision_engine import (
    VisionStateManager,
    get_vision_service
)
import actions.vision_engine as ve

def run_stress_test():
    print("==========================================================")
    # 1. Start Vision Mode
    ve.start_vision_mode()
    time.sleep(1.0)
    
    initial_threads = threading.active_count()
    thread_names = [t.name for t in threading.enumerate()]
    print(f"Initial active thread count: {initial_threads}")
    print(f"Threads: {thread_names}")
    
    # Count of capture and OCR workers
    cap_count_before = sum(1 for t in threading.enumerate() if "NEXUS_VisionCapture" in t.name)
    ocr_count_before = sum(1 for t in threading.enumerate() if "NEXUS_VisionOCR" in t.name)
    
    # 2. Toggle state 100 times
    state_mgr = VisionStateManager.get_instance()
    t0 = time.perf_counter()
    for i in range(100):
        state_mgr.set_sharing_active(False)
        state_mgr.set_sharing_active(True)
    duration = time.perf_counter() - t0
    
    time.sleep(1.0)
    
    final_threads = threading.active_count()
    final_thread_names = [t.name for t in threading.enumerate()]
    print(f"Final active thread count: {final_threads}")
    print(f"Threads: {final_thread_names}")
    
    cap_count_after = sum(1 for t in threading.enumerate() if "NEXUS_VisionCapture" in t.name)
    ocr_count_after = sum(1 for t in threading.enumerate() if "NEXUS_VisionOCR" in t.name)
    
    print(f"Toggled 100 times in {duration:.4f}s")
    print(f"Capture threads count: Before: {cap_count_before}, After: {cap_count_after}")
    print(f"OCR worker threads count: Before: {ocr_count_before}, After: {ocr_count_after}")
    
    # Safety assertions
    assert cap_count_after == cap_count_before, f"Duplicate capture threads detected: {cap_count_after} vs {cap_count_before}"
    assert ocr_count_after == ocr_count_before, f"Duplicate OCR threads detected: {ocr_count_after} vs {ocr_count_before}"
    assert cap_count_after <= 1, "More than one capture thread active!"
    assert ocr_count_after <= 1, "More than one OCR worker thread active!"
    
    print("\n[PASS] Thread safety and memory leak stress test completed successfully.")
    print("==========================================================")

if __name__ == "__main__":
    run_stress_test()
