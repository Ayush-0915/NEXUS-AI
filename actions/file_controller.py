# actions/file_controller.py
# File management — create, delete, move, rename, list, find, organize, edit, validate, project generation

import shutil
import os
import tempfile
from pathlib import Path
from datetime import datetime
import send2trash

from actions.workspace_registry import (
    register_file_creation,
    register_file_modification,
    register_file_deletion,
    log_action,
    start_transaction,
    complete_transaction
)

def _get_desktop() -> Path:
    """Returns desktop path — works on Windows, Mac, Linux."""
    from core.file_manager import get_desktop_path
    return get_desktop_path()


def _get_downloads() -> Path:
    return Path.home() / "Downloads"


def _resolve_path(raw: str) -> Path:
    """
    Resolves a path from user input.
    Defaults relative or filename paths to the Desktop.
    """
    from core.file_manager import resolve_save_path
    return resolve_save_path(raw, default_to_desktop=True)


def _format_size(bytes_size: int) -> str:
    """Converts bytes to human readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_size < 1024:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024
    return f"{bytes_size:.1f} TB"


def validate_path_safety(path: Path) -> tuple[bool, str]:
    """
    Validates if a path is safe to modify or delete.
    Blocks access to critical system folders and strictly enforces approved workspace boundaries.
    """
    try:
        # Resolve symlinks, junctions, aliases and relative dots completely
        resolved = Path(os.path.realpath(os.path.expanduser(str(path))))
        
        # 1. Block root drive level
        if len(resolved.parts) <= 1:
            return False, "Safety Violation: Cannot perform operations on the root level of the drive."
            
        # 2. Block system directories explicitly
        system_folders = [
            "C:\\Windows",
            "C:\\Program Files",
            "C:\\Program Files (x86)",
            "C:\\ProgramData",
            "C:\\Recovery",
            "C:\\System Volume Information",
            "/etc",
            "/var",
            "/usr",
            "/sys",
            "/proc",
            "/boot"
        ]
        
        resolved_str = str(resolved).lower()
        for folder in system_folders:
            if resolved_str.startswith(folder.lower()):
                return False, f"Safety Violation: Path resides in a protected system directory: {folder}."
                
        # 3. Workspace Boundary Enforcement: Target must reside within approved directories
        allowed_roots = [
            Path(os.path.realpath(os.path.expanduser("~"))),       # Home folder resolving symlinks/junctions
            Path(os.path.realpath(str(Path(__file__).resolve().parent.parent))),  # NEXUS AI local workspace
            Path(os.path.realpath(tempfile.gettempdir()))          # System Temp directory
        ]
        
        is_allowed = False
        for root in allowed_roots:
            try:
                if resolved.is_relative_to(root) or resolved == root:
                    is_allowed = True
                    break
            except Exception:
                continue
                
        if not is_allowed:
            return False, f"Safety Violation: Path '{resolved}' escapes the approved workspaces sandbox."
            
        return True, ""
    except Exception as e:
        return False, f"Safety Violation: Path resolution error: {e}"

def list_files(path: str = "desktop", show_hidden: bool = False) -> str:
    """Lists files and folders in a directory."""
    try:
        target = _resolve_path(path)
        
        # Apply safety validation on read/listing actions as well
        is_safe, err = validate_path_safety(target)
        if not is_safe:
            log_action("list_files", path, "failure", err)
            return err
            
        if not target.exists():
            log_action("list_files", path, "failure", "Path not found")
            return f"Path not found: {target}"
        if not target.is_dir():
            log_action("list_files", path, "failure", "Not a directory")
            return f"Not a directory: {target}"

        items = []
        for item in sorted(target.iterdir()):
            if not show_hidden and item.name.startswith("."):
                continue
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                size = _format_size(item.stat().st_size)
                items.append(f"📄 {item.name} ({size})")

        log_action("list_files", str(target), "success", f"Listed {len(items)} items")

        if not items:
            return f"Directory is empty: {target}"

        return f"Contents of {target.name}/ ({len(items)} items):\n" + "\n".join(items)

    except PermissionError:
        log_action("list_files", path, "failure", "Permission denied")
        return f"Permission denied: {path}"
    except Exception as e:
        log_action("list_files", path, "failure", str(e))
        return f"Error listing files: {e}"


def create_file(path: str, content: str = "") -> str:
    """Creates a new file with optional content."""
    try:
        target = Path(path).expanduser()
        
        is_safe, err = validate_path_safety(target)
        if not is_safe:
            log_action("create_file", path, "failure", err)
            return err
            
        tx_id = start_transaction("create_file", str(target))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            if not target.exists():
                raise FileNotFoundError("File does not exist on disk after writing.")
            register_file_creation(str(target))
            complete_transaction(tx_id, "success")
        except Exception as e:
            complete_transaction(tx_id, "failure")
            raise e
            
        return f"File created successfully\nAbsolute Path: {target.resolve()}"
    except Exception as e:
        log_action("create_file", path, "failure", str(e))
        return f"Could not create file: {e}"


def create_folder(path: str) -> str:
    """Creates a new folder (and parent folders if needed)."""
    try:
        target = Path(path).expanduser()
        
        is_safe, err = validate_path_safety(target)
        if not is_safe:
            log_action("create_folder", path, "failure", err)
            return err
            
        tx_id = start_transaction("create_folder", str(target))
        try:
            target.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                raise FileNotFoundError("Directory does not exist after creation.")
            complete_transaction(tx_id, "success")
        except Exception as e:
            complete_transaction(tx_id, "failure")
            raise e
            
        log_action("create_folder", str(target), "success", "Created folder")
        return f"Folder created successfully\nAbsolute Path: {target.resolve()}"
    except Exception as e:
        log_action("create_folder", path, "failure", str(e))
        return f"Could not create folder: {e}"


def delete_file(path: str, confirm: bool = True) -> str:
    """
    Deletes a file or folder.
    Moves to Recycle Bin on Windows if possible, otherwise permanent delete.
    """
    try:
        target = Path(path).expanduser()

        is_safe, err = validate_path_safety(target)
        if not is_safe:
            log_action("delete", path, "failure", err)
            return err

        if not target.exists():
            log_action("delete", path, "failure", "Not found")
            return f"Not found: {path}"

        tx_id = start_transaction("delete", str(target))
        try:
            register_file_deletion(str(target))

            try:
                send2trash.send2trash(str(target))
                complete_transaction(tx_id, "success")
                return f"Moved to Recycle Bin: {target.name}"
            except Exception:
                pass

            # Fallback: permanent delete
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
                
            complete_transaction(tx_id, "success")
        except Exception as e:
            complete_transaction(tx_id, "failure")
            raise e

        return f"Deleted permanently: {target.name}"

    except PermissionError:
        log_action("delete", path, "failure", "Permission denied")
        return f"Permission denied: {path}"
    except Exception as e:
        log_action("delete", path, "failure", str(e))
        return f"Could not delete: {e}"


def move_file(source: str, destination: str) -> str:
    """Moves a file or folder to a new location."""
    try:
        src  = Path(source).expanduser()
        dst  = _resolve_path(destination)

        is_safe_src, err_src = validate_path_safety(src)
        is_safe_dst, err_dst = validate_path_safety(dst)
        if not is_safe_src or not is_safe_dst:
            err = err_src or err_dst
            log_action("move", source, "failure", err)
            return err

        if not src.exists():
            log_action("move", source, "failure", "Source not found")
            return f"Source not found: {source}"

        tx_id = start_transaction("move", str(src))
        try:
            if dst.is_dir():
                dst = dst / src.name

            dst.parent.mkdir(parents=True, exist_ok=True)
            
            register_file_deletion(str(src))
            shutil.move(str(src), str(dst))
            if not dst.exists():
                raise FileNotFoundError("Destination does not exist after move.")
            register_file_creation(str(dst))
            complete_transaction(tx_id, "success")
        except Exception as e:
            complete_transaction(tx_id, "failure")
            raise e
        
        return f"File moved successfully\nAbsolute Path: {dst.resolve()}"

    except Exception as e:
        log_action("move", source, "failure", str(e))
        return f"Could not move: {e}"


def copy_file(source: str, destination: str) -> str:
    """Copies a file or folder."""
    try:
        src = Path(source).expanduser()
        dst = _resolve_path(destination)

        is_safe_src, err_src = validate_path_safety(src)
        is_safe_dst, err_dst = validate_path_safety(dst)
        if not is_safe_src or not is_safe_dst:
            err = err_src or err_dst
            log_action("copy", source, "failure", err)
            return err

        if not src.exists():
            log_action("copy", source, "failure", "Source not found")
            return f"Source not found: {source}"

        tx_id = start_transaction("copy", str(src))
        try:
            if dst.is_dir():
                dst = dst / src.name

            dst.parent.mkdir(parents=True, exist_ok=True)

            if src.is_dir():
                shutil.copytree(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))

            if not dst.exists():
                raise FileNotFoundError("Destination does not exist after copy.")
            register_file_creation(str(dst))
            complete_transaction(tx_id, "success")
        except Exception as e:
            complete_transaction(tx_id, "failure")
            raise e

        return f"File copied successfully\nAbsolute Path: {dst.resolve()}"

    except Exception as e:
        log_action("copy", source, "failure", str(e))
        return f"Could not copy: {e}"


def rename_file(path: str, new_name: str) -> str:
    """Renames a file or folder."""
    try:
        target   = Path(path).expanduser()
        new_path = target.parent / new_name

        is_safe_tgt, err_tgt = validate_path_safety(target)
        is_safe_new, err_new = validate_path_safety(new_path)
        if not is_safe_tgt or not is_safe_new:
            err = err_tgt or err_new
            log_action("rename", path, "failure", err)
            return err

        if not target.exists():
            log_action("rename", path, "failure", "Not found")
            return f"Not found: {path}"
        if new_path.exists():
            log_action("rename", path, "failure", "Destination exists")
            return f"A file named '{new_name}' already exists."

        tx_id = start_transaction("rename", str(target))
        try:
            register_file_deletion(str(target))
            target.rename(new_path)
            if not new_path.exists():
                raise FileNotFoundError("Target does not exist after rename.")
            register_file_creation(str(new_path))
            complete_transaction(tx_id, "success")
        except Exception as e:
            complete_transaction(tx_id, "failure")
            raise e
        
        return f"File renamed successfully\nAbsolute Path: {new_path.resolve()}"

    except Exception as e:
        log_action("rename", path, "failure", str(e))
        return f"Could not rename: {e}"


def read_file(path: str, max_chars: int = 3000) -> str:
    """Reads and returns the content of a text file."""
    try:
        target = Path(path).expanduser()
        
        is_safe, err = validate_path_safety(target)
        if not is_safe:
            log_action("read", path, "failure", err)
            return err
            
        if not target.exists():
            log_action("read", path, "failure", "File not found")
            return f"File not found: {path}"
        if not target.is_file():
            log_action("read", path, "failure", "Not a file")
            return f"Not a file: {path}"

        content = target.read_text(encoding="utf-8", errors="ignore")
        log_action("read", str(target), "success", f"Read {len(content)} chars")
        
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n... (truncated, {len(content)} total chars)"
        return content

    except Exception as e:
        log_action("read", path, "failure", str(e))
        return f"Could not read file: {e}"


def write_file(path: str, content: str, append: bool = False) -> str:
    """Writes or appends content to a file."""
    try:
        target = Path(path).expanduser()
        
        is_safe, err = validate_path_safety(target)
        if not is_safe:
            log_action("write", path, "failure", err)
            return err
            
        tx_id = start_transaction("write", str(target))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            exists = target.exists()
            mode = "a" if append else "w"
            
            with open(target, mode, encoding="utf-8") as f:
                f.write(content)
                
            if not target.exists():
                raise FileNotFoundError("File does not exist on disk after writing.")
                
            if not exists:
                register_file_creation(str(target))
            else:
                register_file_modification(str(target))
                
            complete_transaction(tx_id, "success")
        except Exception as e:
            complete_transaction(tx_id, "failure")
            raise e
            
        return f"File created successfully\nAbsolute Path: {target.resolve()}"
    except Exception as e:
        log_action("write", path, "failure", str(e))
        return f"Could not write file: {e}"


def edit_file(path: str, edit_type: str, content: str, target: str = "", position: str = "after") -> str:
    """
    Edits a text file by appending, replacing, or inserting content relative to a target string.
    """
    try:
        target_path = Path(path).expanduser()
        
        is_safe, err = validate_path_safety(target_path)
        if not is_safe:
            log_action("edit", path, "failure", err)
            return err

        if not target_path.exists():
            log_action("edit", path, "failure", "File does not exist")
            return f"Error: File does not exist: {path}"
        if not target_path.is_file():
            log_action("edit", path, "failure", "Target is not a file")
            return f"Error: Target path is not a file: {path}"

        tx_id = start_transaction("edit", str(target_path))
        try:
            file_text = target_path.read_text(encoding="utf-8", errors="ignore")

            if edit_type == "append":
                new_text = file_text + content
            elif edit_type == "replace":
                if not target:
                    complete_transaction(tx_id, "failure")
                    log_action("edit", path, "failure", "Missing target for replace")
                    return "Error: Replacement requires a 'target' substring to search for."
                if target not in file_text:
                    complete_transaction(tx_id, "failure")
                    log_action("edit", path, "failure", f"Target substring '{target}' not found")
                    return f"Error: Target substring '{target}' not found in file."
                new_text = file_text.replace(target, content)
            elif edit_type == "insert":
                if not target:
                    if position == "start":
                        new_text = content + file_text
                    else:
                        new_text = file_text + content
                else:
                    if target not in file_text:
                        complete_transaction(tx_id, "failure")
                        log_action("edit", path, "failure", f"Target substring '{target}' not found")
                        return f"Error: Target substring '{target}' not found in file."
                    
                    if position == "before":
                        new_text = file_text.replace(target, content + target, 1)
                    else:  # after
                        new_text = file_text.replace(target, target + content, 1)
            else:
                complete_transaction(tx_id, "failure")
                log_action("edit", path, "failure", f"Unknown edit_type '{edit_type}'")
                return f"Error: Unknown edit_type '{edit_type}'."

            target_path.write_text(new_text, encoding="utf-8")
            register_file_modification(str(target_path))
            complete_transaction(tx_id, "success")
        except Exception as e:
            complete_transaction(tx_id, "failure")
            raise e
        
        verification = validate_file(str(target_path), "exists")
        return f"File edited successfully\nAbsolute Path: {target_path.resolve()}"
        
    except Exception as e:
        log_action("edit", path, "failure", str(e))
        return f"Error during edit: {e}"


def validate_file(path: str, validation_type: str) -> str:
    """
    Checks attributes of a path: 'exists', 'size', or 'permissions'.
    """
    try:
        target_path = Path(path).expanduser()
        
        # Apply safety checks on validation targets as well
        is_safe, err = validate_path_safety(target_path)
        if not is_safe:
            log_action("validate", path, "failure", err)
            return err
            
        exists = target_path.exists()
        is_file = target_path.is_file() if exists else False
        is_dir = target_path.is_dir() if exists else False

        if validation_type == "exists":
            status = "exists" if exists else "does not exist"
            type_str = "File" if is_file else ("Folder" if is_dir else "None")
            log_action("validate", path, "success", f"Validated existence: {status}")
            return f"Validation Report:\n  Path: {target_path}\n  Status: {status}\n  Type: {type_str}"

        if not exists:
            log_action("validate", path, "failure", "Path does not exist")
            return f"Validation Report:\n  Path: {target_path}\n  Status: does not exist\n  Error: Cannot validate '{validation_type}' on non-existent path."

        if validation_type == "size":
            if is_file:
                size_bytes = target_path.stat().st_size
                size_str = _format_size(size_bytes)
                log_action("validate", path, "success", f"Validated size: {size_bytes} bytes")
                return f"Validation Report:\n  Path: {target_path}\n  Status: exists\n  Size: {size_bytes} bytes ({size_str})"
            elif is_dir:
                total_size = sum(f.stat().st_size for f in target_path.rglob("*") if f.is_file())
                size_str = _format_size(total_size)
                log_action("validate", path, "success", f"Validated directory size: {total_size} bytes")
                return f"Validation Report:\n  Path: {target_path}\n  Status: exists (Folder)\n  Total Size: {total_size} bytes ({size_str})"

        if validation_type == "permissions":
            readable = os.access(target_path, os.R_OK)
            writable = os.access(target_path, os.W_OK)
            executable = os.access(target_path, os.X_OK)
            perms = []
            if readable: perms.append("READ")
            if writable: perms.append("WRITE")
            if executable: perms.append("EXECUTE")
            log_action("validate", path, "success", f"Validated permissions: {perms}")
            return f"Validation Report:\n  Path: {target_path}\n  Status: exists\n  Permissions: {', '.join(perms) if perms else 'NONE'}"

        log_action("validate", path, "failure", f"Unknown validation_type '{validation_type}'")
        return f"Error: Unknown validation_type '{validation_type}'."
    except Exception as e:
        log_action("validate", path, "failure", str(e))
        return f"Error during validation: {e}"


def find_files(name: str = "", extension: str = "", path: str = "home",
               max_results: int = 20) -> str:
    """Searches for files by name or extension."""
    try:
        search_path = _resolve_path(path)
        
        is_safe, err = validate_path_safety(search_path)
        if not is_safe:
            log_action("find", path, "failure", err)
            return err
            
        if not search_path.exists():
            log_action("find", path, "failure", "Search path not found")
            return f"Search path not found: {path}"

        results = []
        pattern = f"*{extension}" if extension else "*"

        for item in search_path.rglob(pattern):
            if item.is_file():
                if name and name.lower() not in item.name.lower():
                    continue
                size = _format_size(item.stat().st_size)
                results.append(f"📄 {item.name} ({size}) — {item.parent}")
                if len(results) >= max_results:
                    break

        log_action("find", str(search_path), "success", f"Found {len(results)} files")

        if not results:
            query = name or extension or "files"
            return f"No {query} found in {search_path.name}/"

        return f"Found {len(results)} file(s):\n" + "\n".join(results)

    except Exception as e:
        log_action("find", path, "failure", str(e))
        return f"Search error: {e}"


def get_largest_files(path: str = "home", count: int = 10) -> str:
    """Returns the largest files in a directory."""
    try:
        search_path = _resolve_path(path)
        
        is_safe, err = validate_path_safety(search_path)
        if not is_safe:
            log_action("largest", path, "failure", err)
            return err
            
        if not search_path.exists():
            log_action("largest", path, "failure", "Path not found")
            return f"Path not found: {path}"

        files = []
        for item in search_path.rglob("*"):
            if item.is_file():
                try:
                    files.append((item.stat().st_size, item))
                except Exception:
                    continue

        files.sort(reverse=True)
        top = files[:count]

        log_action("largest", str(search_path), "success", f"Found largest {len(top)} files")

        if not top:
            return "No files found."

        lines = [f"Top {len(top)} largest files in {search_path.name}/:\n"]
        for size, f in top:
            lines.append(f"  {_format_size(size):>10}  {f.name}  ({f.parent})")

        return "\n".join(lines)

    except Exception as e:
        log_action("largest", path, "failure", str(e))
        return f"Error: {e}"


def get_disk_usage(path: str = "home") -> str:
    """Returns disk usage information."""
    try:
        target = _resolve_path(path)
        
        is_safe, err = validate_path_safety(target)
        if not is_safe:
            log_action("disk_usage", path, "failure", err)
            return err
            
        usage  = shutil.disk_usage(target)
        total  = _format_size(usage.total)
        used   = _format_size(usage.used)
        free   = _format_size(usage.free)
        pct    = usage.used / usage.total * 100

        log_action("disk_usage", str(target), "success", "Retrieved disk usage")

        return (
            f"Disk usage for {target}:\n"
            f"  Total : {total}\n"
            f"  Used  : {used} ({pct:.1f}%)\n"
            f"  Free  : {free}"
        )
    except Exception as e:
        log_action("disk_usage", path, "failure", str(e))
        return f"Could not get disk usage: {e}"


def organize_desktop() -> str:
    """
    Organizes the desktop by grouping files into folders by type.
    Creates folders: Images, Documents, Videos, Music, Archives, Others
    """
    try:
        desktop = _get_desktop()
        
        is_safe_desk, err_desk = validate_path_safety(desktop)
        if not is_safe_desk:
            log_action("organize_desktop", "desktop", "failure", err_desk)
            return err_desk
            
        type_map = {
            "Images":    [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico"],
            "Documents": [".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".csv"],
            "Videos":    [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"],
            "Music":     [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma"],
            "Archives":  [".zip", ".rar", ".7z", ".tar", ".gz"],
            "Code":      [".py", ".js", ".html", ".css", ".json", ".xml", ".ts", ".cpp", ".java"],
        }

        moved    = []
        skipped  = []

        tx_id = start_transaction("organize_desktop", str(desktop))
        try:
            for item in desktop.iterdir():
                if item.is_dir() or item.name.startswith("."):
                    continue

                ext        = item.suffix.lower()
                target_dir = None

                for folder, extensions in type_map.items():
                    if ext in extensions:
                        target_dir = desktop / folder
                        break

                if target_dir is None:
                    target_dir = desktop / "Others"

                is_safe, err = validate_path_safety(target_dir / item.name)
                if not is_safe:
                    skipped.append(f"{item.name} (Safety block)")
                    continue

                target_dir.mkdir(exist_ok=True)
                new_path = target_dir / item.name

                if new_path.exists():
                    skipped.append(item.name)
                    continue

                register_file_deletion(str(item))
                shutil.move(str(item), str(new_path))
                register_file_creation(str(new_path))
                moved.append(f"{item.name} → {target_dir.name}/")
                
            complete_transaction(tx_id, "success")
        except Exception as e:
            complete_transaction(tx_id, "failure")
            raise e

        log_action("organize_desktop", str(desktop), "success", f"Moved {len(moved)} files")

        result = f"Desktop organized. {len(moved)} files moved."
        if moved:
            result += "\n" + "\n".join(moved[:10])
            if len(moved) > 10:
                result += f"\n... and {len(moved)-10} more."
        if skipped:
            result += f"\n{len(skipped)} files skipped (already exist or safety blocked)."

        return result

    except Exception as e:
        log_action("organize_desktop", "desktop", "failure", str(e))
        return f"Could not organize desktop: {e}"


def get_file_info(path: str) -> str:
    """Returns detailed information about a file."""
    try:
        target = Path(path).expanduser()
        
        is_safe, err = validate_path_safety(target)
        if not is_safe:
            log_action("info", path, "failure", err)
            return err
            
        if not target.exists():
            log_action("info", path, "failure", "Not found")
            return f"Not found: {path}"

        stat = target.stat()
        info = {
            "Name":     target.name,
            "Type":     "Folder" if target.is_dir() else "File",
            "Size":     _format_size(stat.st_size),
            "Location": str(target.parent),
            "Created":  datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M"),
            "Modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "Extension": target.suffix or "None",
        }

        log_action("info", str(target), "success", "Retrieved file info")
        return "\n".join(f"  {k}: {v}" for k, v in info.items())

    except Exception as e:
        log_action("info", path, "failure", str(e))
        return f"Could not get file info: {e}"

def where_is_file(filename: str) -> str:
    """
    Searches for a file in:
    - Desktop
    - NexusProjects
    - workspaces
    - project directories (from registry)
    Returns the absolute path if found.
    """
    from core.file_manager import get_desktop_path
    from actions.workspace_registry import _load_registry
    
    filename = filename.strip()
    if not filename:
        return "No filename provided."
        
    search_roots = []
    
    # 1. Desktop
    desktop = get_desktop_path()
    search_roots.append(("Desktop", desktop))
    
    # 2. NexusProjects
    nexus_projects = desktop / "NexusProjects"
    if nexus_projects.exists():
        search_roots.append(("NexusProjects", nexus_projects))
        
    # 3. Workspaces (e.g. project_root)
    workspace_root = Path(__file__).resolve().parent.parent
    search_roots.append(("Workspace", workspace_root))
    
    # 4. Registered project directories
    try:
        registry = _load_registry()
        for proj_name, proj_info in registry.get("projects", {}).items():
            proj_path = Path(proj_info.get("path", ""))
            if proj_path.exists() and proj_path not in [r[1] for r in search_roots]:
                search_roots.append((f"Project '{proj_name}'", proj_path))
    except Exception:
        pass
        
    found_paths = []
    for root_name, root_path in search_roots:
        try:
            for item in root_path.rglob("*"):
                if item.is_file() and item.name.lower() == filename.lower():
                    found_paths.append(item.resolve())
        except Exception:
            continue
            
    unique_paths = list(set(found_paths))
    
    if unique_paths:
        paths_str = "\n".join(f"- {p}" for p in unique_paths)
        return f"File found:\n{paths_str}"
        
    return f"File '{filename}' not found in Desktop, NexusProjects, workspaces, or registered project directories."


def file_controller(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None
) -> str:
    action  = (parameters or {}).get("action", "").lower().strip()
    path    = (parameters or {}).get("path", "desktop")
    name    = (parameters or {}).get("name", "")
    content = (parameters or {}).get("content", "")

    def _full_path(p: str, n: str) -> str:
        base = _resolve_path(p)
        if n:
            return str(base / n)
        return str(base)

    result = "Unknown action."

    try:
        if action == "list":
            result = list_files(path)

        elif action == "create_file":
            full = _full_path(path, name)
            result = create_file(full, content=content)

        elif action == "create_folder":
            full = _full_path(path, name)
            result = create_folder(full)

        elif action == "delete":
            full = _full_path(path, name)
            result = delete_file(full)

        elif action == "move":
            full = _full_path(path, name)
            result = move_file(full, parameters.get("destination", ""))

        elif action == "copy":
            full = _full_path(path, name)
            result = copy_file(full, parameters.get("destination", ""))

        elif action == "rename":
            full = _full_path(path, name)
            result = rename_file(full, parameters.get("new_name", ""))

        elif action == "read":
            full = _full_path(path, name)
            result = read_file(full)

        elif action == "write":
            full = _full_path(path, name)
            result = write_file(
                full,
                content=content,
                append=parameters.get("append", False)
            )

        elif action == "find":
            result = find_files(
                name=name or parameters.get("name", ""),
                extension=parameters.get("extension", ""),
                path=path,
                max_results=parameters.get("max_results", 20)
            )

        elif action == "largest":
            result = get_largest_files(
                path=path,
                count=parameters.get("count", 10)
            )

        elif action == "disk_usage":
            result = get_disk_usage(path)

        elif action == "organize_desktop":
            result = organize_desktop()

        elif action == "info":
            full = _full_path(path, name)
            result = get_file_info(full)

        elif action == "edit":
            full = _full_path(path, name)
            result = edit_file(
                full,
                edit_type=parameters.get("edit_type", "append"),
                content=content,
                target=parameters.get("target", ""),
                position=parameters.get("position", "after")
            )

        elif action == "validate":
            full = _full_path(path, name)
            result = validate_file(full, parameters.get("validation_type", "exists"))

        elif action == "where_is_file":
            result = where_is_file(name or parameters.get("filename", ""))

        elif action == "generate_project":
            from actions.project_generator import generate_project
            proj_desc = parameters.get("project_description", content)
            proj_name = parameters.get("project_name", name)
            report = generate_project(proj_desc, proj_name, path)
            result = f"Project Generator Output:\n{json.dumps(report, indent=2)}"

        else:
            result = f"Unknown action: '{action}'"

    except Exception as e:
        result = f"File controller error: {e}"

    if player:
        player.write_log(f"[file] {result[:60]}")

    return result