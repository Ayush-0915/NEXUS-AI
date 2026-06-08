# scratch/test_fs_baseline.py
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
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

print("Project root path:", project_root)

# Check imports
try:
    from actions.file_controller import (
        create_folder,
        create_file,
        write_file,
        read_file,
        list_files,
        get_file_info
    )
    print("SUCCESS: Imported file_controller tools!")
except Exception as e:
    print("FAILURE: Failed to import file_controller tools:", e)
    sys.exit(1)

# Target directory
desktop = Path.home() / "Desktop"
test_dir = desktop / "NEXUS_TEST"

print(f"\n--- Running Step 2 Functional Test: Creating {test_dir} ---")

# Step 2: Functional Testing
# Create Desktop/NEXUS_TEST/
# Inside create: Documents/, Code/, Data/, Logs/
subdirs = ["Documents", "Code", "Data", "Logs"]
results = {}

for subdir in subdirs:
    target_path = test_dir / subdir
    print(f"Creating folder: {target_path}")
    res = create_folder(str(target_path))
    print(f"Result: {res}")
    results[subdir] = target_path.exists()

# Create files:
# Documents/notes.txt
# Documents/report.md
# Code/main.py
# Code/README.md
# Data/config.json
# Data/sample.csv
# Logs/activity.log

files_to_create = {
    "Documents/notes.txt": "Meeting notes:\n- Review file system capabilities\n- Audit existing features\n- Implement improvements",
    "Documents/report.md": "# NEXUS AI File System Audit Report\n\nThis is a report containing details of the functional tests performed on the NEXUS AI file system capability.",
    "Code/main.py": "def main():\n    print('Hello from NEXUS AI!')\n\nif __name__ == '__main__':\n    main()",
    "Code/README.md": "# NEXUS Project\n\nPython codebase for automated task testing.",
    "Data/config.json": '{\n  "version": "1.0.0",\n  "debug": true,\n  "settings": {\n    "theme": "dark",\n    "notifications": false\n  }\n}',
    "Data/sample.csv": "id,name,value\n1,AI System,Active\n2,Memory,Synced\n3,File System,Audited",
    "Logs/activity.log": "2026-06-08 10:45:00 - INFO - System initialization\n2026-06-08 10:45:05 - INFO - Baseline test started"
}

for rel_path, content in files_to_create.items():
    file_path = test_dir / rel_path
    print(f"Creating file: {file_path}")
    res = create_file(str(file_path), content=content)
    print(f"Result: {res}")
    results[rel_path] = file_path.exists() and file_path.read_text(encoding="utf-8") == content

# Verification
print("\n--- Verification Report ---")
all_folders_ok = all(results[sd] for sd in subdirs)
all_files_ok = all(results[fp] for fp in files_to_create)

print(f"Folder creation succeeded: {all_folders_ok}")
print(f"File creation and content writing succeeded: {all_files_ok}")

for item, success in results.items():
    status = "SUCCESS" if success else "FAILED"
    print(f"  {item}: {status}")

if all_folders_ok and all_files_ok:
    print("\nOVERALL TEST STATUS: PASSED")
else:
    print("\nOVERALL TEST STATUS: FAILED")
