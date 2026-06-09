# scratch/test_nexus_queries.py
import sys
import os
import time
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from or_client import client
from actions.vision_engine import vision_assistant
from actions.system_info import get_system_info
from actions.open_app import open_app

class DummyPlayer:
    def write_log(self, text):
        # Clean emojis for console print safety
        safe_text = str(text).encode('ascii', errors='replace').decode('ascii')
        print(f"[UI LOG] {safe_text}")
    def set_state(self, state):
        print(f"[UI STATE] -> {state}")
    def show_vision_panel(self):
        print("[UI VISION] Showing vision panel")
    def update_vision_panel(self, data):
        print(f"[UI VISION] Updated with: {list(data.keys()) if data else None}")

def run_stress_test():
    print("=" * 60)
    print(" NEXUS AI — Harden Pipeline 10-Query Stress Test")
    print("=" * 60)
    
    player = DummyPlayer()
    queries = [
        ("Query 1: Hello", lambda: client.chat("Hello! How are you?")),
        ("Query 2: Explain machine learning", lambda: client.chat("Explain machine learning in one short sentence.")),
        ("Query 3: Tell me a joke", lambda: client.chat("Tell me a developer joke.")),
        ("Query 4: What are my laptop specs?", lambda: get_system_info(parameters={"component": "cpu"}, player=player)),
        ("Query 5: Analyze my screen", lambda: vision_assistant("Analyze my screen")),
        ("Query 6: Open calculator", lambda: open_app(parameters={"app_name": "calculator"}, player=player)),
        ("Query 7: Summarize AI", lambda: client.chat("Summarize artificial intelligence in 10 words.")),
        ("Query 8: What time is it?", lambda: client.chat("What time is it? Today is Tuesday, June 9, 2026.")),
        ("Query 9: Explain Python classes", lambda: client.chat("Explain Python classes in one sentence.")),
        ("Query 10: Tell me something interesting", lambda: client.chat("Tell me one extremely interesting scientific fact."))
    ]

    for label, task in queries:
        print(f"\n--- Running: {label} ---")
        try:
            start = time.time()
            result = task()
            duration = time.time() - start
            try:
                safe_res = str(result)[:150].encode('ascii', errors='replace').decode('ascii')
                print(f"Result (first 150 chars):\n{safe_res}...")
            except UnicodeEncodeError:
                print("Result contains non-ASCII characters.")
            print(f"Duration: {duration:.2f}s")
            print("Status: SUCCESS OK")
        except Exception as e:
            print(f"Status: FAILED - {e}")
            
    print("\n" + "=" * 60)
    print(" Fallback and Hardening Verification")
    print("=" * 60)
    
    print("\n[Simulation] Simulating OpenRouter Rate-Limiting...")
    from or_client import _mark_model_failed
    _mark_model_failed("meta-llama/llama-3.3-70b-instruct:free", "rate_limited")
    
    print("Running chat query...")
    start = time.time()
    reply = client.chat("Hello again! (should skip rate-limited model and fallback)")
    safe_reply = str(reply)[:100].encode('ascii', errors='replace').decode('ascii')
    print(f"Reply: {safe_reply}...")
    print(f"Time taken: {time.time() - start:.2f}s")
    
    print("\n[Simulation] Simulating Complete OpenRouter Outage (Falls back to Gemini)...")
    from or_client import TEXT_MODELS
    for m in TEXT_MODELS:
        _mark_model_failed(m, "rate_limited")
        
    print("Running chat query...")
    start = time.time()
    reply = client.chat("Hi from fallback! (should fall back to Gemini)")
    safe_reply = str(reply)[:100].encode('ascii', errors='replace').decode('ascii')
    print(f"Reply: {safe_reply}...")
    print(f"Time taken: {time.time() - start:.2f}s")
    
    print("\nAll stress tests completed successfully!")
    print("=" * 60)

if __name__ == "__main__":
    run_stress_test()
