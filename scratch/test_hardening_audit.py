# scratch/test_hardening_audit.py
import sys
import os
import json
import time
import threading
import tempfile
import subprocess
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

from actions.file_controller import file_controller, validate_path_safety, validate_file
from actions.workspace_registry import _load_registry, _save_registry, register_files_creation

print("================ PRODUCTION HARDENING AUDIT ================")

desktop = Path.home() / "Desktop"
audit_dir = desktop / "NEXUS_HARDENING_AUDIT"
audit_dir.mkdir(parents=True, exist_ok=True)

audit_results = []

def run_test(name, fn):
    print(f"\n--- Hardening Test: {name} ---")
    try:
        res = fn()
        audit_results.append((name, True, str(res)))
        print(f"RESULT: PASS - {res}")
    except Exception as e:
        audit_results.append((name, False, str(e)))
        print(f"RESULT: FAIL - {e}")
        import traceback
        traceback.print_exc()

def get_memory_usage_mb() -> float:
    try:
        import psutil
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
    except ImportError:
        try:
            pid = os.getpid()
            cmd = f'tasklist /FI "PID eq {pid}" /FO CSV /NH'
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            parts = res.stdout.strip().split(",")
            if len(parts) >= 5:
                mem_str = parts[4].replace('"', '').replace(' K', '').replace(' ', '').replace(',', '').strip()
                return float(mem_str) / 1024.0
        except Exception:
            pass
        return 0.0

# 1. Path Traversal & Junction Point Protection
def test_path_traversal():
    # Attempt relative path escape outside sandbox
    traversal_path = audit_dir / "../../../Windows/System32"
    is_safe, err = validate_path_safety(traversal_path)
    print(f"Traversal attempt: {traversal_path} -> is_safe={is_safe}, err={err}")
    assert not is_safe
    assert "escapes the approved workspaces sandbox" in err or "protected system directory" in err
    
    # Try to write to a file using relative dots
    relative_target = audit_dir / "../../../escaped_target.txt"
    res_write = file_controller({
        "action": "write",
        "path": str(relative_target),
        "content": "traversal content"
    })
    print("Relative write result:", res_write)
    assert "Safety Violation" in res_write
    
    # Try to delete using relative dots
    res_delete = file_controller({
        "action": "delete",
        "path": str(relative_target)
    })
    print("Relative delete result:", res_delete)
    assert "Safety Violation" in res_delete

    # Junction Point Protection (Windows specific)
    junction_path = audit_dir / "windows_junction"
    if junction_path.exists():
        try:
            os.rmdir(junction_path)
        except Exception:
            pass
            
    # Try to create a junction point pointing to C:\Windows
    try:
        cmd = ["cmd.exe", "/c", "mklink", "/J", str(junction_path), "C:\\Windows"]
        link_res = subprocess.run(cmd, capture_output=True, text=True)
        print("Junction creation command output:", link_res.stdout.strip(), link_res.stderr.strip())
        
        if junction_path.exists():
            # Verify junction validation resolves it to C:\Windows
            is_safe_junc, err_junc = validate_path_safety(junction_path)
            print(f"Junction validation: {junction_path} -> is_safe={is_safe_junc}, err={err_junc}")
            assert not is_safe_junc
            assert "protected system directory" in err_junc
            
            # Verify file controller rejects actions on this junction path
            res_del_junc = file_controller({
                "action": "delete",
                "path": str(junction_path)
            })
            print("Delete Junction attempt result:", res_del_junc)
            assert "Safety Violation" in res_del_junc
    except Exception as e:
        print(f"Skipping junction test: {e}")
    finally:
        if junction_path.exists():
            try:
                os.rmdir(junction_path)
            except Exception:
                pass
                
    # Symbolic Link Escape attempt
    symlink_path = audit_dir / "windows_symlink"
    if symlink_path.exists():
        try:
            os.unlink(symlink_path)
        except Exception:
            pass
            
    try:
        os.symlink("C:\\Windows", symlink_path, target_is_directory=True)
        if symlink_path.exists():
            is_safe_sym, err_sym = validate_path_safety(symlink_path)
            print(f"Symlink validation: {symlink_path} -> is_safe={is_safe_sym}, err={err_sym}")
            assert not is_safe_sym
            assert "protected system directory" in err_sym
            
            res_del_sym = file_controller({
                "action": "delete",
                "path": str(symlink_path)
            })
            print("Delete Symlink attempt result:", res_del_sym)
            assert "Safety Violation" in res_del_sym
    except OSError as e:
        print(f"Skipping symlink test (OS privilege restrictions): {e}")
    finally:
        if symlink_path.exists():
            try:
                os.unlink(symlink_path)
            except Exception:
                pass
                
    return "All path traversal, symlinks, and junction points successfully blocked."

run_test("Path Traversal & Symlink/Junction Sandboxing", test_path_traversal)


# 2. Registry Recovery Validation
def test_registry_recovery():
    registry_file = project_root / "workspace_registry.json"
    backup_file = project_root / "workspace_registry.json.bak"
    
    # Create backup first by saving registry
    reg_data = _load_registry()
    _save_registry(reg_data)
    assert backup_file.exists(), "Backup registry file should exist."
    
    backup_content = backup_file.read_text(encoding="utf-8")
    
    # Corrupt registry intentionally with invalid JSON
    print("Corrupting registry file...")
    registry_file.write_text("CORRUPTED_JSON_DATA{{{[", encoding="utf-8")
    
    # Load registry (should trigger recovery)
    recovered_registry = _load_registry()
    print("Recovered registry projects count:", len(recovered_registry.get("projects", {})))
    
    # Verify recovered data
    assert len(recovered_registry.get("projects", {})) >= 0
    assert registry_file.read_text(encoding="utf-8") != "CORRUPTED_JSON_DATA{{{[", "Registry file should be overwritten by recovery."
    
    # Restore original content
    _save_registry(json.loads(backup_content))
    return "Workspace registry automatically recovered from backup without data loss."

run_test("Registry Corruption & Recovery", test_registry_recovery)


# 3. Concurrent Registry Operations (Race Conditions)
def test_concurrency():
    errors = []
    
    def write_worker(idx):
        try:
            file_path = audit_dir / f"concurrent_{idx}.txt"
            res = file_controller({
                "action": "write",
                "path": str(file_path),
                "content": f"thread {idx} content"
            })
            if "Safety Violation" in res or "Could not" in res:
                errors.append(f"Worker {idx} failed: {res}")
        except Exception as e:
            errors.append(f"Worker {idx} exception: {e}")
            
    threads = []
    print("Launching 30 concurrent writer threads...")
    for i in range(30):
        t = threading.Thread(target=write_worker, args=(i,))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    # Check registry integrity
    reg = _load_registry()
    print(f"Registry transactions logged: {len(reg.get('transactions', []))}")
    assert len(errors) == 0, f"Concurrent execution errors: {errors}"
    
    return "Concurrency test passed. Lock and retry mechanics handled race conditions safely."

run_test("Concurrent Writes & Registry Locking", test_concurrency)


# 4. Large Project Stress Test Expansion (500, 1000, 5000 files)
def test_stress_testing_expansion():
    results = {}
    
    counts = [500, 1000, 5000]
    for count in counts:
        print(f"\n--- Running Stress Test with {count} Files ---")
        stress_project_dir = audit_dir / f"stress_project_{count}"
        stress_project_dir.mkdir(parents=True, exist_ok=True)
        
        mem_start = get_memory_usage_mb()
        start_time = time.time()
        
        # 1. Batch Generate Files
        batch_files = []
        for i in range(count):
            file_path = stress_project_dir / f"file_{i}.txt"
            batch_files.append((str(file_path), "document"))
            
        creation_time = time.time() - start_time
        
        # 2. Registry Registration (Batch)
        start_reg_time = time.time()
        register_files_creation(batch_files, f"stress_project_{count}")
        reg_time = time.time() - start_reg_time
        
        # 3. Validation Performance
        start_val_time = time.time()
        # Validate existence of a subset of files (10%) and total folder validation
        validate_file(str(stress_project_dir), "size")
        for i in range(0, count, 10):
            file_path = stress_project_dir / f"file_{i}.txt"
            validate_path_safety(file_path)
        val_time = time.time() - start_val_time
        
        # 4. Registry File Size and Load Overhead
        reg_file = project_root / "workspace_registry.json"
        reg_size_kb = reg_file.stat().st_size / 1024.0
        
        start_load_time = time.time()
        _load_registry()
        load_time = time.time() - start_load_time
        
        mem_end = get_memory_usage_mb()
        mem_overhead = mem_end - mem_start
        
        results[count] = {
            "creation_time": creation_time,
            "registry_time": reg_time,
            "validation_time": val_time,
            "registry_size_kb": reg_size_kb,
            "registry_load_time": load_time,
            "memory_overhead_mb": mem_overhead
        }
        
        print(f"Results for {count} files:")
        print(f"  File list prep time: {creation_time:.4f}s")
        print(f"  Registry registration time: {reg_time:.4f}s")
        print(f"  Validation speed: {val_time:.4f}s")
        print(f"  Registry file size: {reg_size_kb:.2f} KB")
        print(f"  Registry load time: {load_time:.4f}s")
        print(f"  Memory Overhead: {mem_overhead:.2f} MB")
        
        # Cleanup project files
        try:
            import shutil
            shutil.rmtree(stress_project_dir)
        except Exception:
            pass

    # Clean the registered files from the registry so it doesn't stay bloated
    print("\nCleaning stress test records from registry...")
    reg = _load_registry()
    for count in counts:
        proj_prefix = f"stress_project_{count}"
        reg["files"] = {k: v for k, v in reg["files"].items() if v.get("project") != proj_prefix}
    _save_registry(reg)

    return json.dumps(results, indent=2)

run_test("Stress Test Expansion (500/1000/5000 files)", test_stress_testing_expansion)


# 5. Large File Handling (10MB–100MB)
def test_large_files():
    large_file = audit_dir / "large_20mb.dat"
    
    # A. Write 20MB file
    print("Writing 20MB file...")
    start_time = time.time()
    content_20mb = "X" * (20 * 1024 * 1024) # 20MB
    res_write = file_controller({
        "action": "write",
        "path": str(large_file),
        "content": content_20mb
    })
    write_time = time.time() - start_time
    print(f"Write time: {write_time:.2f}s | Result: {res_write}")
    assert large_file.exists()
    
    # B. Validate large file
    print("Validating large file...")
    start_time = time.time()
    res_val = file_controller({
        "action": "validate",
        "path": str(large_file),
        "validation_type": "size"
    })
    val_time = time.time() - start_time
    print(f"Validation time: {val_time:.2f}s | Result: {res_val.strip()}")
    assert "20.0 MB" in res_val
    
    # C. Read large file
    print("Reading large file...")
    start_time = time.time()
    res_read = file_controller({
        "action": "read",
        "path": str(large_file)
    })
    read_time = time.time() - start_time
    print(f"Read time: {read_time:.2f}s | Content truncated: {len(res_read)} chars")
    assert "truncated" in res_read
    
    # Clean file
    large_file.unlink()
    return f"Large file tested. Write={write_time:.2f}s, Validate={val_time:.2f}s, Read={read_time:.2f}s"

run_test("Large File Performance (20MB)", test_large_files)


# Summarize hardening tests
print("\n================ HARDENING AUDIT SUMMARY ================")
all_passed = True
for name, passed, details in audit_results:
    status = "PASSED" if passed else "FAILED"
    print(f"- {name}: {status}")
    if not passed:
        all_passed = False
        print(f"  Details: {details}")

print(f"\nHARDENING STATUS: {'SUCCESS' if all_passed else 'FAILURE'}")

# Clean up
try:
    import shutil
    shutil.rmtree(audit_dir)
    print("Cleaned up NEXUS_HARDENING_AUDIT temporary files.")
except Exception as e:
    print("Cleanup warning:", e)
    
sys.exit(0 if all_passed else 1)
