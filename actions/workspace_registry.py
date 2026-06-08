# actions/workspace_registry.py
import json
import os
import uuid
import threading
from pathlib import Path
from datetime import datetime

# Thread-safe lock for concurrency protection
_registry_lock = threading.Lock()

def _get_registry_path() -> Path:
    return Path(__file__).resolve().parent.parent / "workspace_registry.json"

def _get_backup_path() -> Path:
    return Path(__file__).resolve().parent.parent / "workspace_registry.json.bak"

def _load_registry() -> dict:
    path = _get_registry_path()
    backup = _get_backup_path()
    
    # Try loading main registry
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WorkspaceRegistry] Main registry corrupted: {e}. Attempting recovery from backup...")
            # Recovery process from backup
            if backup.exists():
                try:
                    data = json.loads(backup.read_text(encoding="utf-8"))
                    # Restore main registry from backup
                    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                    print("[WorkspaceRegistry] Successfully recovered registry from backup.")
                    return data
                except Exception as recovery_error:
                    print(f"[WorkspaceRegistry] Backup also corrupted: {recovery_error}.")
            
    # Fallback to empty registry
    return {
        "projects": {},
        "files": {},
        "actions_log": [],
        "transactions": []
    }

def _save_registry(registry: dict):
    path = _get_registry_path()
    backup = _get_backup_path()
    tmp_path = path.with_suffix(".json.tmp")
    
    # 1. Create backup of current file if it exists and is valid
    if path.exists():
        try:
            # Verify current file is valid json before backing up
            current_data = json.loads(path.read_text(encoding="utf-8"))
            backup.write_text(json.dumps(current_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass # Skip backup if current is invalid
            
    # 2. Write to temporary file, verify integrity, then swap atomically
    try:
        # Write content to temporary file
        tmp_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
        # Flush OS buffer to disk
        with open(tmp_path, "a", encoding="utf-8") as f:
            os.fsync(f.fileno())
            
        # Verify integrity: parse the written file back to guarantee no corruption
        temp_content = tmp_path.read_text(encoding="utf-8")
        json.loads(temp_content)
        
        # 3. Rename/Swap atomically with retries for Windows file locking contention (WinError 5)
        import time
        for attempt in range(5):
            try:
                os.replace(str(tmp_path), str(path))
                break
            except OSError as e:
                if attempt == 4:
                    raise e
                time.sleep(0.05 * (attempt + 1))
    except Exception as e:
        print(f"[WorkspaceRegistry] Error saving registry atomically: {e}")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        raise e

def register_project(project_name: str, project_path: str, description: str = ""):
    with _registry_lock:
        registry = _load_registry()
        registry["projects"][project_name] = {
            "path": str(Path(project_path).resolve()),
            "created_at": datetime.now().isoformat(),
            "description": description
        }
        _save_registry(registry)
    log_action("register_project", project_path, "success", f"Registered project '{project_name}'")

def register_file_creation(file_path: str, asset_type: str = "other", project_name: str = None):
    with _registry_lock:
        registry = _load_registry()
        resolved_path = str(Path(file_path).resolve())
        registry["files"][resolved_path] = {
            "created_at": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "modifications_count": 0,
            "project": project_name,
            "asset_type": asset_type if asset_type != "other" else _detect_asset_type(Path(file_path))
        }
        _save_registry(registry)
    log_action("create_file", file_path, "success", f"Created file with asset type '{asset_type}'")

def register_file_modification(file_path: str):
    with _registry_lock:
        registry = _load_registry()
        resolved_path = str(Path(file_path).resolve())
        if resolved_path in registry["files"]:
            registry["files"][resolved_path]["last_modified"] = datetime.now().isoformat()
            registry["files"][resolved_path]["modifications_count"] += 1
        else:
            registry["files"][resolved_path] = {
                "created_at": datetime.now().isoformat(),
                "last_modified": datetime.now().isoformat(),
                "modifications_count": 1,
                "project": None,
                "asset_type": _detect_asset_type(Path(file_path))
            }
        _save_registry(registry)
    log_action("modify_file", file_path, "success", "Modified file content")

def register_file_deletion(file_path: str):
    with _registry_lock:
        registry = _load_registry()
        resolved_path = str(Path(file_path).resolve())
        if resolved_path in registry["files"]:
            del registry["files"][resolved_path]
        _save_registry(registry)
    log_action("delete_file", file_path, "success", "Deleted file from workspace")

def log_action(action_name: str, path: str, status: str, details: str):
    with _registry_lock:
        registry = _load_registry()
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action_name,
            "path": str(Path(path).resolve()) if path else "",
            "status": status,
            "details": details
        }
        registry["actions_log"].append(log_entry)
        if len(registry["actions_log"]) > 500:
            registry["actions_log"] = registry["actions_log"][-500:]
        _save_registry(registry)

def start_transaction(action: str, target_path: str) -> str:
    """Starts a filesystem operation transaction and returns a unique Transaction ID."""
    with _registry_lock:
        registry = _load_registry()
        if "transactions" not in registry:
            registry["transactions"] = []
            
        tx_id = str(uuid.uuid4())
        tx_entry = {
            "operation_id": tx_id,
            "transaction_id": tx_id,
            "timestamp": datetime.now().isoformat(),
            "target_path": str(Path(target_path).resolve()) if target_path else "",
            "action": action,
            "status": "pending"
        }
        registry["transactions"].append(tx_entry)
        if len(registry["transactions"]) > 200:
            registry["transactions"] = registry["transactions"][-200:]
        _save_registry(registry)
    return tx_id

def complete_transaction(tx_id: str, status: str = "success"):
    """Completes a transaction with a success or failure status."""
    with _registry_lock:
        registry = _load_registry()
        if "transactions" in registry:
            for tx in registry["transactions"]:
                if tx["transaction_id"] == tx_id:
                    tx["status"] = status
                    break
            _save_registry(registry)

def register_files_creation(files_info: list, project_name: str = None):
    """
    Registers multiple file creations in a single registry transaction.
    files_info is a list of tuples: (file_path, asset_type)
    """
    with _registry_lock:
        registry = _load_registry()
        for file_path, asset_type in files_info:
            resolved_path = str(Path(file_path).resolve())
            registry["files"][resolved_path] = {
                "created_at": datetime.now().isoformat(),
                "last_modified": datetime.now().isoformat(),
                "modifications_count": 0,
                "project": project_name,
                "asset_type": asset_type if asset_type != "other" else _detect_asset_type(Path(file_path))
            }
        _save_registry(registry)
    log_action("create_files_batch", "", "success", f"Created {len(files_info)} files in batch")

def _detect_asset_type(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    code_exts = {"py", "js", "ts", "jsx", "tsx", "html", "css", "java", "c", "cpp", "go", "rs", "sh"}
    doc_exts = {"txt", "md", "pdf", "docx", "doc", "pptx", "ppt"}
    data_exts = {"csv", "json", "xml", "xlsx", "xls", "yaml", "yml"}
    log_exts = {"log"}
    
    if ext in code_exts: return "code"
    if ext in doc_exts: return "document"
    if ext in data_exts: return "data"
    if ext in log_exts: return "log"
    return "other"
