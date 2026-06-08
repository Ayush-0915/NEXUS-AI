# scratch/test_system_awareness.py
import sys
import os
from pathlib import Path

# Configure UTF-8 encoding for standard output to avoid UnicodeEncodeErrors
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent.parent.parent.parent.parent / "OneDrive" / "Desktop" / "Private" / "NEXUS AI"
sys.path.insert(0, str(project_root))

print("Project root path:", project_root)

# Check imports
try:
    from actions.system_info import (
        get_performance_metrics,
        check_system_health,
        get_running_apps,
        get_hardware_recommendations,
        diagnose_system,
        analyze_storage,
    )
    print("SUCCESS: Imported system-awareness tools!")
except Exception as e:
    print("FAILURE: Failed to import system-awareness tools:", e)
    sys.exit(1)

# Run each tool and print a sample of the output
print("\n--- Testing: get_performance_metrics() ---")
try:
    perf = get_performance_metrics()
    print(perf)
except Exception as e:
    print("Error:", e)

print("\n--- Testing: check_system_health() ---")
try:
    health = check_system_health()
    print(health)
except Exception as e:
    print("Error:", e)

print("\n--- Testing: get_running_apps() ---")
try:
    apps = get_running_apps()
    print(apps)
except Exception as e:
    print("Error:", e)

print("\n--- Testing: get_hardware_recommendations() ---")
try:
    rec = get_hardware_recommendations(parameters={"query": "Can I upgrade my SSD?"})
    print(rec)
except Exception as e:
    print("Error:", e)

print("\n--- Testing: diagnose_system() ---")
try:
    diag = diagnose_system()
    print(diag)
except Exception as e:
    print("Error:", e)

print("\n--- Testing: analyze_storage() ---")
try:
    storage = analyze_storage()
    print(storage)
except Exception as e:
    print("Error:", e)
