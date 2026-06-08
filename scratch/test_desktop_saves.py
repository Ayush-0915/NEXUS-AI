# scratch/test_desktop_saves.py
import sys
import os
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from core.file_manager import get_desktop_path, is_desktop_available
from actions.file_controller import file_controller

print("================ DESKTOP ENFORCEMENT VALIDATION ================")

desktop_path = get_desktop_path()
print(f"Detected Desktop Path: {desktop_path}")
print(f"Desktop exists & writable: {is_desktop_available()}")

# 1. Validation Tests: Create test.txt, test.pdf, test.docx, test.md on Desktop
test_files = ["test.txt", "test.pdf", "test.docx", "test.md"]
creation_results = {}

for filename in test_files:
    print(f"\n--- Creating: {filename} ---")
    # Call file_controller write action without specifying path, so it defaults to Desktop
    res = file_controller({
        "action": "write",
        "path": filename,
        "content": f"Nexus test content for {filename}"
    })
    print("Response from NEXUS:")
    print(res)
    
    # Verify the file physically exists on the detected Desktop
    target_path = desktop_path / filename
    exists_on_disk = target_path.exists()
    print(f"Physically exists on Desktop: {exists_on_disk}")
    
    # Verify success string format rules
    assert "File created successfully" in res
    assert f"Absolute Path: {target_path.resolve()}" in res
    assert exists_on_disk
    
    creation_results[filename] = str(target_path.resolve())

# 2. Test "where is file" command on the created files
print("\n--- Testing where_is_file Action ---")
for filename in test_files:
    print(f"Searching for: {filename}")
    res = file_controller({
        "action": "where_is_file",
        "name": filename
    })
    print(res)
    assert "File found" in res
    assert creation_results[filename] in res

# Cleanup validation files
print("\nCleaning up test files from Desktop...")
for filename in test_files:
    target_path = desktop_path / filename
    if target_path.exists():
        target_path.unlink()
        print(f"Deleted: {filename}")

print("\nVALIDATION STATUS: SUCCESS - All file saves enforced on Desktop successfully.")
