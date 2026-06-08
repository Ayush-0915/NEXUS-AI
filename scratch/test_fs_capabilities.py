# scratch/test_fs_capabilities.py
import sys
import os
import json
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

# Import capabilities
try:
    from actions.file_controller import (
        file_controller,
        validate_path_safety,
        validate_file,
        edit_file,
        create_file,
        delete_file
    )
    from actions.project_generator import generate_project
    print("SUCCESS: Imported file controller, validation, editing, safety, and project generator modules!")
except Exception as e:
    print("FAILURE: Failed to import capabilities:", e)
    sys.exit(1)

test_dir = Path.home() / "Desktop" / "NEXUS_CAPABILITY_TEST"
test_dir.mkdir(parents=True, exist_ok=True)

test_results = []

def run_test(name, fn):
    print(f"\n--- Running Test: {name} ---")
    try:
        res = fn()
        test_results.append((name, True, str(res)))
        print(f"RESULT: PASS - {res}")
    except Exception as e:
        test_results.append((name, False, str(e)))
        print(f"RESULT: FAIL - {e}")

# 1. File Creation & Nested Directory Creation
def test_file_creation():
    folder_path = test_dir / "SubFolder" / "NestedFolder"
    file_path = folder_path / "test_create.txt"
    
    # Clean first
    if file_path.exists():
        file_path.unlink()
    if folder_path.exists():
        os.removedirs(folder_path)
        
    res_folder = file_controller({"action": "create_folder", "path": str(folder_path)})
    res_file = file_controller({"action": "create_file", "path": str(folder_path), "name": "test_create.txt", "content": "Initial File Content"})
    
    assert file_path.exists(), "File should exist on disk."
    assert file_path.read_text(encoding="utf-8") == "Initial File Content", "Content should match."
    return f"Folder: {res_folder} | File: {res_file}"

run_test("File & Nested Directory Creation", test_file_creation)


# 2. File Editing (Append, Replace, Insert)
def test_file_editing():
    file_path = test_dir / "test_edit.txt"
    file_path.write_text("Hello World\nLine 2\nLine 3", encoding="utf-8")
    
    # A. Append Content
    res_append = file_controller({
        "action": "edit",
        "path": str(file_path),
        "edit_type": "append",
        "content": "\nAppended Line"
    })
    
    # B. Replace Content
    res_replace = file_controller({
        "action": "edit",
        "path": str(file_path),
        "edit_type": "replace",
        "target": "World",
        "content": "Universe"
    })
    
    # C. Insert Before Target
    res_insert_before = file_controller({
        "action": "edit",
        "path": str(file_path),
        "edit_type": "insert",
        "target": "Line 2",
        "content": "Inserted Before Line 2\n",
        "position": "before"
    })
    
    # D. Insert After Target
    res_insert_after = file_controller({
        "action": "edit",
        "path": str(file_path),
        "edit_type": "insert",
        "target": "Line 2",
        "content": "\nInserted After Line 2",
        "position": "after"
    })
    
    final_content = file_path.read_text(encoding="utf-8")
    print(f"Final Content after edits:\n{final_content}")
    
    assert "Appended Line" in final_content
    assert "Universe" in final_content
    assert "Inserted Before Line 2" in final_content
    assert "Inserted After Line 2" in final_content
    return "All edits succeeded"

run_test("File Editing Options", test_file_editing)


# 3. File Validation (Exists, Size, Permissions)
def test_file_validation():
    file_path = test_dir / "test_validate.txt"
    file_path.write_text("Validation Content", encoding="utf-8")
    
    # Exists
    res_exists = file_controller({"action": "validate", "path": str(file_path), "validation_type": "exists"})
    # Size
    res_size = file_controller({"action": "validate", "path": str(file_path), "validation_type": "size"})
    # Permissions
    res_perms = file_controller({"action": "validate", "path": str(file_path), "validation_type": "permissions"})
    
    assert "exists" in res_exists.lower()
    assert "18 bytes" in res_size.lower()
    assert "READ" in res_perms
    return f"Exists: {res_exists.strip()} | Size: {res_size.strip()} | Perms: {res_perms.strip()}"

run_test("File Validation Reports", test_file_validation)


# 4. Safety Layer Restrictions
def test_safety_layer():
    protected_paths = [
        "C:\\Windows\\System32\\cmd.exe",
        "C:\\Program Files\\Internet Explorer",
        "C:\\Program Files (x86)\\Microsoft",
        "C:\\ProgramData\\Nexus",
        "C:\\"
    ]
    
    for path in protected_paths:
        p = Path(path)
        is_safe, err = validate_path_safety(p)
        print(f"Path: {path} -> is_safe: {is_safe}, err: {err}")
        assert not is_safe, f"Path '{path}' should NOT be safe."
        assert "Safety Violation" in err, f"Error should mention safety violation. Got: {err}"
        
        # Test controller actions reject it
        res_write = file_controller({"action": "write", "path": path, "content": "unsafe"})
        print(f"res_write for {path}: {res_write}")
        assert "Safety Violation" in res_write, f"Write to '{path}' should have failed with safety violation. Got: {res_write}"
        
        res_delete = file_controller({"action": "delete", "path": path})
        print(f"res_delete for {path}: {res_delete}")
        assert "Safety Violation" in res_delete, f"Delete of '{path}' should have failed with safety violation. Got: {res_delete}"
        
    return "Safety layer correctly blocked all critical folders."

run_test("Safety Layer Restrictions", test_safety_layer)


# 5. Project Generator Service
def test_project_generator():
    # Test standard template generation
    report_flask = file_controller({
        "action": "generate_project",
        "path": str(test_dir),
        "name": "my_flask_project",
        "content": "Create a Flask web app boilerplate."
    })
    
    flask_dir = test_dir / "my_flask_project"
    assert flask_dir.exists(), "Flask project dir should be created."
    assert (flask_dir / "app.py").exists(), "app.py should be created."
    assert (flask_dir / "templates" / "index.html").exists(), "templates/index.html should be created."
    
    return "Boilerplate project generation verified."

run_test("Project Generator Service", test_project_generator)


# 6. Workspace Registry Audit
def test_workspace_registry():
    registry_path = project_root / "workspace_registry.json"
    assert registry_path.exists(), "Registry file workspace_registry.json should exist."
    
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert "projects" in registry
    assert "files" in registry
    assert "actions_log" in registry
    
    assert "my_flask_project" in registry["projects"]
    assert len(registry["actions_log"]) > 0
    return f"Workspace registry matches schema. Found {len(registry['projects'])} projects, {len(registry['files'])} files, and {len(registry['actions_log'])} logged actions."

run_test("Workspace Registry Audit", test_workspace_registry)


# Final test summaries
print("\n================ TEST SUMMARY ================")
all_passed = True
for name, passed, details in test_results:
    status = "PASSED" if passed else "FAILED"
    print(f"- {name}: {status}")
    if not passed:
        all_passed = False
        print(f"  Details: {details}")

print(f"\nOVERALL RESULT: {'SUCCESS' if all_passed else 'FAILURE'}")

# Clean test files
try:
    # Recursively delete test_dir
    import shutil
    shutil.rmtree(test_dir)
    print("Cleaned up NEXUS_CAPABILITY_TEST directory.")
except Exception as e:
    print("Cleanup warning:", e)
    
sys.exit(0 if all_passed else 1)
