# scratch/test_e2e_scenarios.py
import sys
import os
import json
from pathlib import Path

# Configure UTF-8 encoding for standard output
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from actions.file_controller import file_controller, validate_path_safety
from actions.project_generator import generate_project

print("================ E2E SCENARIO VALIDATION ================")

desktop = Path.home() / "Desktop"
test_dir = desktop / "NEXUS_E2E_TEST"
test_dir.mkdir(parents=True, exist_ok=True)

# Helper to print directory trees
def print_dir_tree(dir_path: Path, prefix: str = ""):
    if not dir_path.exists():
        print(f"{prefix}Path does not exist: {dir_path}")
        return
    for item in sorted(dir_path.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_dir():
            print(f"{prefix}├── 📁 {item.name}/")
            print_dir_tree(item, prefix + "│   ")
        else:
            print(f"{prefix}├── 📄 {item.name} ({item.stat().st_size} B)")

# 1. Flask and React template generation
print("\n--- 1. Generating Flask Boilerplate Project ---")
flask_report = generate_project("Create a Flask api app with styling", "e2e_flask_app", str(test_dir))
print("Flask Report:", json.dumps(flask_report, indent=2))
print_dir_tree(test_dir / "e2e_flask_app")

print("\n--- 2. Generating React Boilerplate Project ---")
react_report = generate_project("Create a React frontend dashboard", "e2e_react_app", str(test_dir))
print("React Report:", json.dumps(react_report, indent=2))
print_dir_tree(test_dir / "e2e_react_app")

# 2. Custom project generation (Gemini API fallback)
print("\n--- 3. Generating Custom Project via Gemini Fallback ---")
custom_desc = "Create a small python utility that reads CSV files and outputs stats. Need a main.py and a data/stats.py."
custom_report = generate_project(custom_desc, "e2e_custom_utility", str(test_dir))
print("Custom Report:", json.dumps(custom_report, indent=2))
print_dir_tree(test_dir / "e2e_custom_utility")

# 3. Create, edit, validate, and read files
print("\n--- 4. Manipulating files in Custom Project ---")
custom_main = test_dir / "e2e_custom_utility" / "main.py"
print("Initial main.py content:")
print(custom_main.read_text(encoding="utf-8") if custom_main.exists() else "Not found")

# Edit: Append import
print("Editing: Appending log helper call...")
res_edit = file_controller({
    "action": "edit",
    "path": str(custom_main),
    "edit_type": "append",
    "content": "\n# End of Utility Script - Verified E2E"
})
print("Edit Result:", res_edit)

# Read edited content
print("Reading file content:")
read_res = file_controller({
    "action": "read",
    "path": str(custom_main)
})
print("File content after edit:\n", read_res)

# Validate file
print("Validating size:")
size_res = file_controller({
    "action": "validate",
    "path": str(custom_main),
    "validation_type": "size"
})
print("Size validation result:", size_res)

# 4. Attempt operations inside protected directories
print("\n--- 5. Testing Safety Layer (Protected Paths) ---")
protected = "C:\\Windows\\System32\\drivers\\etc\\hosts"
print(f"Trying to write to: {protected}")
safety_write_res = file_controller({
    "action": "write",
    "path": protected,
    "content": "127.0.0.1 unsafe.com"
})
print("Write safety check result:", safety_write_res)

print(f"Trying to delete: {protected}")
safety_delete_res = file_controller({
    "action": "delete",
    "path": protected
})
print("Delete safety check result:", safety_delete_res)

# 5. Confirm actions are recorded in workspace_registry.json
print("\n--- 6. Auditing workspace_registry.json ---")
registry_path = project_root / "workspace_registry.json"
if registry_path.exists():
    registry_data = json.loads(registry_path.read_text(encoding="utf-8"))
    print(f"Total projects registered: {len(registry_data.get('projects', {}))}")
    print(f"Total files registered: {len(registry_data.get('files', {}))}")
    print(f"Total action log entries: {len(registry_data.get('actions_log', []))}")
    print("\nRecent 5 action log entries:")
    for entry in registry_data.get("actions_log", [])[-5:]:
        print(f"  [{entry.get('timestamp')}] {entry.get('action')} -> {entry.get('status')} | {entry.get('details')}")
else:
    print("Error: registry file not found!")

# Clean test files
try:
    import shutil
    shutil.rmtree(test_dir)
    print("\nCleaned up E2E temporary test directory.")
except Exception as e:
    print("Cleanup warning:", e)
