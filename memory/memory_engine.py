import json
import sqlite3
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta
from threading import Lock

# Import from memory_manager
from memory.memory_manager import (
    BASE_DIR, DB_PATH, _db_lock,
    add_semantic_memory, search_semantic_memory,
    update_capability_index
)
from memory.config_manager import get_gemini_key


class MemoryEngine:
    _instance = None
    _lock = Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(MemoryEngine, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.current_session_id = None

    def start_session(self, active_workspace: str = None) -> int:
        with _db_lock:
            try:
                conn = sqlite3.connect(str(DB_PATH))
                cursor = conn.cursor()
                # Deactivate previous active sessions
                cursor.execute("UPDATE sessions SET is_active = 0, end_time = CURRENT_TIMESTAMP WHERE is_active = 1")
                
                cursor.execute("""
                INSERT INTO sessions (active_workspace, is_active)
                VALUES (?, 1)
                """, (active_workspace,))
                session_id = cursor.lastrowid
                conn.commit()
                conn.close()
                
                self.current_session_id = session_id
                print(f"[MemoryEngine] Active session set to {session_id}.")
                
                # Log a development event
                self.add_semantic_memory(
                    category="DEVELOPMENT",
                    content=f"Started new session {session_id} on workspace: {active_workspace}",
                    importance_score=4,
                    privacy_mode="PUBLIC"
                )
                return session_id
            except Exception as e:
                print(f"[MemoryEngine] start_session failed: {e}")
                return -1

    def end_session(self, session_id: int = None, skip_llm: bool = False) -> str:
        target_id = session_id if session_id is not None else self.current_session_id
        if target_id is None:
            # Look for active session in DB
            with _db_lock:
                try:
                    conn = sqlite3.connect(str(DB_PATH))
                    cursor = conn.cursor()
                    cursor.execute("SELECT session_id FROM sessions WHERE is_active = 1 ORDER BY start_time DESC LIMIT 1")
                    row = cursor.fetchone()
                    conn.close()
                    if row:
                        target_id = row[0]
                except Exception:
                    pass

        if target_id is None:
            return "No active session to end."

        with _db_lock:
            try:
                conn = sqlite3.connect(str(DB_PATH))
                cursor = conn.cursor()
                cursor.execute("UPDATE sessions SET is_active = 0, end_time = CURRENT_TIMESTAMP WHERE session_id = ?", (target_id,))
                
                # Fetch turns
                cursor.execute("SELECT role, content FROM chat_turns WHERE session_id = ? ORDER BY timestamp ASC", (target_id,))
                turns = cursor.fetchall()
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[MemoryEngine] SQLite end_session error: {e}")
                turns = []

        summary = ""
        if turns:
            chat_log = "\n".join(f"{role.upper()}: {content}" for role, content in turns)
            gemini_key = get_gemini_key()
            if gemini_key and os.environ.get("NEXUS_TEST_MODE") != "1" and not skip_llm:
                try:
                    from or_client import client
                    prompt = (
                        f"Summarize the following developer chat session in a single brief paragraph. "
                        f"Focus on what was built, fixed, or discussed.\n\n"
                        f"Chat logs:\n{chat_log}\n\n"
                        f"Summary:"
                    )
                    summary = client.chat(prompt, system="You are the NEXUS Summarization Engine. Be concise.", max_tokens=200)
                    summary = summary.strip()
                except Exception as e:
                    print(f"[MemoryEngine] LLM session summarization failed: {e}")
            
            if not summary:
                summary = f"Developer session with {len(turns)} chat turns completed. Actions logged."

        if summary:
            with _db_lock:
                try:
                    conn = sqlite3.connect(str(DB_PATH))
                    cursor = conn.cursor()
                    cursor.execute("UPDATE sessions SET summary = ? WHERE session_id = ?", (summary, target_id))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    print(f"[MemoryEngine] Failed to write session summary: {e}")

        # Add to semantic memory
        if summary:
            self.add_semantic_memory(
                category="PROJECT",
                content=f"Session summary (ID: {target_id}): {summary}",
                importance_score=5,
                privacy_mode="PUBLIC",
                source_reference_id=target_id
            )

        if target_id == self.current_session_id:
            self.current_session_id = None

        return f"Session {target_id} ended. Summary saved."

    def log_chat_turn(self, role: str, content: str, session_id: int = None) -> None:
        target_id = session_id if session_id is not None else self.current_session_id
        if target_id is None:
            # Create a session if not active
            target_id = self.start_session(active_workspace=str(BASE_DIR))

        with _db_lock:
            try:
                conn = sqlite3.connect(str(DB_PATH))
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO chat_turns (session_id, role, content)
                VALUES (?, ?, ?)
                """, (target_id, role, content))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[MemoryEngine] log_chat_turn error: {e}")

        # Also log to semantic memory for searchability
        # Assign lower importance for normal chat queries (1-3) unless it's a specific capability update
        importance = 2
        if "bug" in content.lower() or "fix" in content.lower():
            importance = 6
        elif "architecture" in content.lower() or "design" in content.lower():
            importance = 8

        self.add_semantic_memory(
            category="CHAT",
            content=f"{role.upper()}: {content}",
            importance_score=importance,
            privacy_mode="PUBLIC",
            source_reference_id=target_id
        )

    def save_workspace_snapshot(self, active_project: str, open_files: list, active_branch: str = "main", current_task: str = "", session_summary: str = "") -> None:
        target_id = self.current_session_id
        if target_id is None:
            target_id = self.start_session(active_workspace=active_project)

        open_files_json = json.dumps(open_files) if isinstance(open_files, list) else str(open_files)
        with _db_lock:
            try:
                conn = sqlite3.connect(str(DB_PATH))
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO workspace_snapshots (session_id, active_project, open_files, active_branch, current_task, session_summary)
                VALUES (?, ?, ?, ?, ?, ?)
                """, (target_id, active_project, open_files_json, active_branch, current_task, session_summary))
                conn.commit()
                conn.close()
                print(f"[MemoryEngine] Saved workspace snapshot for session {target_id}.")
            except Exception as e:
                print(f"[MemoryEngine] save_workspace_snapshot failed: {e}")

    def get_latest_workspace_snapshot(self) -> dict:
        with _db_lock:
            try:
                conn = sqlite3.connect(str(DB_PATH))
                cursor = conn.cursor()
                cursor.execute("""
                SELECT snapshot_id, session_id, timestamp, active_project, open_files, active_branch, current_task, session_summary
                FROM workspace_snapshots
                ORDER BY timestamp DESC LIMIT 1
                """)
                row = cursor.fetchone()
                conn.close()
            except Exception as e:
                print(f"[MemoryEngine] Failed to get latest snapshot: {e}")
                row = None

        if not row:
            return None

        try:
            open_files = json.loads(row[4])
        except Exception:
            open_files = []

        return {
            "snapshot_id": row[0],
            "session_id": row[1],
            "timestamp": row[2],
            "active_project": row[3],
            "open_files": open_files,
            "active_branch": row[5],
            "current_task": row[6],
            "session_summary": row[7]
        }

    def add_node(self, node_type: str, name: str, path: str = None, metadata: dict = None) -> int:
        metadata_json = json.dumps(metadata) if metadata else None
        with _db_lock:
            try:
                conn = sqlite3.connect(str(DB_PATH))
                cursor = conn.cursor()
                if path:
                    cursor.execute("SELECT node_id FROM graph_nodes WHERE name = ? AND path = ?", (name, path))
                else:
                    cursor.execute("SELECT node_id FROM graph_nodes WHERE name = ? AND path IS NULL", (name,))
                row = cursor.fetchone()
                if row:
                    node_id = row[0]
                    if metadata_json:
                        cursor.execute("UPDATE graph_nodes SET metadata = ? WHERE node_id = ?", (metadata_json, node_id))
                        conn.commit()
                    conn.close()
                    return node_id

                cursor.execute("""
                INSERT INTO graph_nodes (type, name, path, metadata)
                VALUES (?, ?, ?, ?)
                """, (node_type, name, path, metadata_json))
                node_id = cursor.lastrowid
                conn.commit()
                conn.close()
                return node_id
            except Exception as e:
                print(f"[MemoryEngine] add_node error: {e}")
                return -1

    def add_edge(self, source_id: int, target_id: int, relation_type: str, weight: float = 1.0) -> None:
        with _db_lock:
            try:
                conn = sqlite3.connect(str(DB_PATH))
                cursor = conn.cursor()
                cursor.execute("""
                SELECT edge_id FROM graph_edges
                WHERE source_id = ? AND target_id = ? AND relation_type = ?
                """, (source_id, target_id, relation_type))
                row = cursor.fetchone()
                if row:
                    cursor.execute("UPDATE graph_edges SET weight = ?, last_updated = CURRENT_TIMESTAMP WHERE edge_id = ?", (weight, row[0]))
                else:
                    cursor.execute("""
                    INSERT INTO graph_edges (source_id, target_id, relation_type, weight)
                    VALUES (?, ?, ?, ?)
                    """, (source_id, target_id, relation_type, weight))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[MemoryEngine] add_edge error: {e}")

    def add_semantic_memory(self, category: str, content: str, importance_score: int, privacy_mode: str, aging_stage: str = 'RECENT', source_reference_id: int = None, is_pinned: bool = False) -> int:
        # Check category correctness
        valid_cats = {'CHAT', 'DEVELOPMENT', 'ACTIVITY', 'PROJECT', 'ARCHITECTURE', 'DECISION', 'BUG', 'FEATURE'}
        if category not in valid_cats:
            category = 'CHAT'
            
        # Check privacy mode
        valid_privacy = {'PUBLIC', 'PRIVATE', 'SYSTEM'}
        if privacy_mode not in valid_privacy:
            privacy_mode = 'PUBLIC'

        return add_semantic_memory(
            category=category,
            content=content,
            importance_score=importance_score,
            privacy_mode=privacy_mode,
            aging_stage=aging_stage,
            source_reference_id=source_reference_id,
            is_pinned=is_pinned
        )

    def search_semantic_memory(self, query: str, limit: int = 5, category: str = None, include_private: bool = False) -> list[dict]:
        return search_semantic_memory(query, limit, category, include_private)

    def consolidate_memories(self) -> None:
        with _db_lock:
            try:
                conn = sqlite3.connect(str(DB_PATH))
                cursor = conn.cursor()
                cursor.execute("""
                SELECT faiss_id, category, content, importance_score, privacy_mode, timestamp
                FROM semantic_metadata
                WHERE is_consolidated = 0
                """)
                rows = cursor.fetchall()
                conn.close()
            except Exception as e:
                print(f"[MemoryEngine] Failed to load unconsolidated memories: {e}")
                rows = []

        if not rows:
            return

        by_category = {}
        for r in rows:
            cat = r[1]
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(r)

        for cat, items in by_category.items():
            if len(items) < 3:
                continue

            contents = [f"- {item[2]} (importance: {item[3]})" for item in items]
            raw_text = "\n".join(contents)
            summary = None
            
            gemini_key = get_gemini_key()
            if gemini_key and os.environ.get("NEXUS_TEST_MODE") != "1":
                try:
                    from or_client import client
                    prompt = (
                        f"You are the NEXUS Consolidation Engine. Consolidate the following raw memory logs of "
                        f"category '{cat}' into a single clean summary memory. Bullet points are fine.\n\n"
                        f"Raw memories:\n{raw_text}\n\n"
                        f"Consolidated memory text:"
                    )
                    summary = client.chat(prompt, system="Be extremely direct. Return only the consolidated text summary.", max_tokens=300)
                    summary = summary.strip()
                except Exception as e:
                    print(f"[MemoryEngine] LLM consolidation failed: {e}")

            if not summary:
                summary = f"Consolidated {cat} development log:\n" + raw_text

            importance = max(item[3] for item in items)
            privacy = 'PUBLIC'
            if any(item[4] == 'SYSTEM' for item in items):
                privacy = 'SYSTEM'
            if any(item[4] == 'PRIVATE' for item in items):
                privacy = 'PRIVATE'

            consolidated_id = self.add_semantic_memory(
                category=cat,
                content=summary,
                importance_score=importance,
                privacy_mode=privacy,
                aging_stage='SHORT_TERM',
                source_reference_id=None
            )

            # Mark raw items as consolidated
            with _db_lock:
                try:
                    conn = sqlite3.connect(str(DB_PATH))
                    cursor = conn.cursor()
                    placeholders = ",".join("?" for _ in items)
                    ids = [item[0] for item in items]
                    cursor.execute(f"UPDATE semantic_metadata SET is_consolidated = 1 WHERE faiss_id IN ({placeholders})", ids)
                    conn.commit()
                    conn.close()
                except Exception as e:
                    print(f"[MemoryEngine] Failed to mark items as consolidated: {e}")

            print(f"[MemoryEngine] Consolidated {len(items)} raw memories in {cat} into memory {consolidated_id}.")

    def run_aging_lifecycle(self) -> None:
        with _db_lock:
            try:
                conn = sqlite3.connect(str(DB_PATH))
                cursor = conn.cursor()
                # Promote RECENT -> SHORT_TERM
                cursor.execute("""
                UPDATE semantic_metadata
                SET aging_stage = 'SHORT_TERM'
                WHERE aging_stage = 'RECENT'
                  AND (importance_score >= 7 OR recall_count >= 3)
                """)
                # Promote SHORT_TERM -> LONG_TERM
                cursor.execute("""
                UPDATE semantic_metadata
                SET aging_stage = 'LONG_TERM'
                WHERE aging_stage = 'SHORT_TERM'
                  AND (importance_score >= 9 OR recall_count >= 6 OR is_consolidated = 1)
                """)
                conn.commit()
                conn.close()
                print("[MemoryEngine] Aging lifecycle promotion rules applied.")
            except Exception as e:
                print(f"[MemoryEngine] run_aging_lifecycle failed: {e}")

    def trigger_auto_analysis(self) -> None:
        try:
            update_capability_index()
        except Exception as e:
            print(f"[MemoryEngine] Capability auto-analysis failed: {e}")

    # ==========================
    # Recall Commands Parser
    # ==========================
    def handle_recall_command(self, command: str) -> str:
        cmd_lower = command.lower().strip()

        # Command 1: "What were we working on yesterday?" / "Show yesterday's session summary"
        if "working on yesterday" in cmd_lower or "yesterday's session" in cmd_lower:
            # Query active or recent sessions from yesterday or last 24h
            with _db_lock:
                try:
                    conn = sqlite3.connect(str(DB_PATH))
                    cursor = conn.cursor()
                    # Query sessions starting from yesterday
                    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
                    cursor.execute("""
                    SELECT session_id, start_time, active_workspace, summary
                    FROM sessions
                    WHERE start_time >= ?
                    ORDER BY start_time DESC
                    """, (yesterday,))
                    rows = cursor.fetchall()
                    conn.close()
                except Exception as e:
                    rows = []
                    print(f"[MemoryEngine] Recall working on yesterday failed: {e}")

            if not rows:
                return "No sessions recorded for yesterday. We can start a new development session!"

            lines = ["Here is what we were working on yesterday:"]
            for r in rows:
                sid, stime, workspace, summary = r
                summary_text = summary if summary else "No session summary recorded yet."
                lines.append(f"- **Session {sid}** ({stime}) on workspace `{workspace}`:\n  {summary_text}")
            return "\n".join(lines)

        # Command 2: "Continue my last coding session"
        if "continue my last coding session" in cmd_lower or "continue last session" in cmd_lower:
            snapshot = self.get_latest_workspace_snapshot()
            if not snapshot:
                return "No workspace snapshot found from your last coding session."
            
            files_list = ", ".join(f"`{f}`" for f in snapshot["open_files"]) if snapshot["open_files"] else "No files open"
            msg = (
                f"Restoring your last coding session from snapshot {snapshot['snapshot_id']} ({snapshot['timestamp']}):\n"
                f"- **Active Project**: `{snapshot['active_project']}`\n"
                f"- **Open Files**: {files_list}\n"
                f"- **Active Branch**: `{snapshot['active_branch']}`\n"
                f"- **Active Task**: {snapshot['current_task'] if snapshot['current_task'] else 'None'}"
            )
            return msg

        # Command 3: "Show architecture decisions"
        if "architecture decisions" in cmd_lower or "show decisions" in cmd_lower:
            res = self.search_semantic_memory(command, limit=5, category="DECISION")
            # If empty, try architecture
            if not res:
                res = self.search_semantic_memory(command, limit=5, category="ARCHITECTURE")

            if not res:
                return "No architecture decisions or decision logs found in memory."

            lines = ["Here are the recent architecture decisions found in memory:"]
            for item in res:
                lines.append(f"- [{item['timestamp']}] ({item['aging_stage']}) Importance {item['importance_score']}/10:\n  {item['content']}")
            return "\n".join(lines)

        # Command 4: "Show Vision Mode history"
        if "vision mode history" in cmd_lower or "show vision history" in cmd_lower:
            res = self.search_semantic_memory(command, limit=5, category="ACTIVITY")
            if not res:
                return "No Vision Mode activity logs found in memory."

            lines = ["Here is the recorded Vision Mode history:"]
            for item in res:
                lines.append(f"- [{item['timestamp']}] {item['content']}")
            return "\n".join(lines)

        # Command 5: "What bugs were fixed last week?"
        if "bugs were fixed last week" in cmd_lower or "fixed bugs" in cmd_lower:
            # Query semantic memories under BUG category
            res = self.search_semantic_memory(command, limit=10, category="BUG")
            if not res:
                return "No bug fix records found in memory for last week."

            lines = ["Here are the bug fixes found in memory:"]
            for item in res:
                lines.append(f"- [{item['timestamp']}] {item['content']}")
            return "\n".join(lines)

        # Command 6: "Show project milestones"
        if "project milestones" in cmd_lower or "show milestones" in cmd_lower:
            # Query high importance category=FEATURE or PROJECT
            res = self.search_semantic_memory(command, limit=10, category="FEATURE")
            # Filter importance >= 8
            milestones = [item for item in res if item["importance_score"] >= 8]
            
            # Try PROJECT if milestones are empty
            if not milestones:
                res = self.search_semantic_memory(command, limit=10, category="PROJECT")
                milestones = [item for item in res if item["importance_score"] >= 8]

            if not milestones:
                return "No project milestones (importance >= 8) found in memory."

            lines = ["Here are the project milestones achieved:"]
            for item in milestones:
                lines.append(f"- [{item['timestamp']}] Importance {item['importance_score']}/10: {item['content']}")
            return "\n".join(lines)

        # General Semantic Search Recall
        res = self.search_semantic_memory(command, limit=5)
        if not res:
            return "No relevant memories found for your query."

        lines = ["Here is what I found in memory relating to your query:"]
        for item in res:
            lines.append(f"- [{item['timestamp']}] ({item['category']}) Importance {item['importance_score']}/10: {item['content']}")
        return "\n".join(lines)

    # ==========================
    # Memory Retention & Cleanup
    # ==========================
    def load_cleanup_config(self) -> dict:
        config_file = Path(__file__).resolve().parent.parent / "config" / "memory_cleanup_config.json"
        defaults = {
            "retention_period_days": 30,
            "automatic_cleanup_enabled": True,
            "max_database_size_bytes": None,
            "dry_run": False,
            "fragmentation_threshold": 0.15,
            "deleted_count_since_last_rebuild": 0
        }
        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
                    defaults.update(user_config)
            except Exception as e:
                print(f"[MemoryEngine] Failed to load cleanup config: {e}")
        return defaults

    def save_cleanup_config(self, config: dict) -> None:
        config_file = Path(__file__).resolve().parent.parent / "config" / "memory_cleanup_config.json"
        try:
            config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"[MemoryEngine] Failed to save cleanup config: {e}")

    def pin_memory(self, faiss_id: int) -> bool:
        import sqlite3
        import memory.memory_manager as mm
        with mm._db_lock:
            try:
                conn = sqlite3.connect(str(mm.DB_PATH))
                cursor = conn.cursor()
                cursor.execute("UPDATE semantic_metadata SET is_pinned = 1 WHERE faiss_id = ?", (faiss_id,))
                success = cursor.rowcount > 0
                conn.commit()
                conn.close()
                if success:
                    print(f"[MemoryEngine] Successfully pinned memory ID {faiss_id}.")
                return success
            except Exception as e:
                print(f"[MemoryEngine] Failed to pin memory {faiss_id}: {e}")
                return False

    def unpin_memory(self, faiss_id: int) -> bool:
        import sqlite3
        import memory.memory_manager as mm
        with mm._db_lock:
            try:
                conn = sqlite3.connect(str(mm.DB_PATH))
                cursor = conn.cursor()
                cursor.execute("UPDATE semantic_metadata SET is_pinned = 0 WHERE faiss_id = ?", (faiss_id,))
                success = cursor.rowcount > 0
                conn.commit()
                conn.close()
                if success:
                    print(f"[MemoryEngine] Successfully unpinned memory ID {faiss_id}.")
                return success
            except Exception as e:
                print(f"[MemoryEngine] Failed to unpin memory {faiss_id}: {e}")
                return False

    def get_database_size_stats(self) -> dict:
        from memory.memory_manager import DB_PATH, FAISS_INDEX_PATH
        db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        faiss_size = FAISS_INDEX_PATH.stat().st_size if FAISS_INDEX_PATH.exists() else 0
        return {
            "sqlite_db_size_bytes": db_size,
            "faiss_index_size_bytes": faiss_size,
            "total_size_bytes": db_size + faiss_size
        }

    def get_last_cleanup_report(self) -> dict:
        report_file = Path(__file__).resolve().parent / "cleanup_report.json"
        if report_file.exists():
            try:
                with open(report_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[MemoryEngine] Failed to load last cleanup report: {e}")
        return {}

    def run_memory_cleanup(self, dry_run=None, force_rebuild=False) -> dict:
        import sqlite3
        import numpy as np
        import memory.memory_manager as mm
        
        config = self.load_cleanup_config()
        retention_days = config.get("retention_period_days", 30)
        max_size = config.get("max_database_size_bytes", None)
        frag_threshold = config.get("fragmentation_threshold", 0.15)
        deleted_since_rebuild = config.get("deleted_count_since_last_rebuild", 0)
        
        actual_dry_run = dry_run if dry_run is not None else config.get("dry_run", False)
        
        stats_before = self.get_database_size_stats()
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        
        def should_keep_memory(row_dict: dict) -> bool:
            category = str(row_dict.get("category", "")).upper()
            content = str(row_dict.get("content", "")).lower()
            importance = row_dict.get("importance_score", 0)
            recall = row_dict.get("recall_count", 0)
            privacy = str(row_dict.get("privacy_mode", "")).upper()
            
            # Keep high importance indefinitely
            if importance >= 7:
                return True
                
            # Keep frequently recalled
            if recall >= 3:
                return True
                
            # Keep architecture decisions
            if category in ('ARCHITECTURE', 'DECISION') or "architecture decision" in content:
                return True
                
            # Keep project milestones
            if "milestone" in content or (category in ('PROJECT', 'FEATURE') and importance >= 8):
                return True
                
            # Keep capabilities
            if "capability" in content or (category == 'FEATURE' and privacy == 'SYSTEM'):
                return True
                
            # Keep user preferences
            if "preference" in content or "favorite" in content or "like" in content or "dislike" in content:
                return True
                
            # Keep critical bug fixes
            if category == 'BUG' or "bug fix" in content or "fixed bug" in content:
                if "critical" in content or importance >= 7:
                    return True
                    
            return False

        def parse_sqlite_timestamp(ts_str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
                try:
                    return datetime.strptime(ts_str.strip(), fmt)
                except ValueError:
                    continue
            return datetime.now()

        # Connect to SQLite
        with mm._db_lock:
            try:
                conn = sqlite3.connect(str(mm.DB_PATH))
                cursor = conn.cursor()
                cursor.execute("""
                SELECT faiss_id, category, content, importance_score, privacy_mode, aging_stage, recall_count, timestamp, is_pinned
                FROM semantic_metadata
                """)
                rows = cursor.fetchall()
                conn.close()
            except Exception as e:
                print(f"[MemoryEngine] Failed to read memories for cleanup: {e}")
                rows = []

        total_scanned = 0
        total_retained = 0
        total_pinned = 0
        total_skipped_due_to_pinning = 0
        to_delete = []
        
        # Classify each memory
        for r in rows:
            row_dict = {
                "faiss_id": r[0], "category": r[1], "content": r[2],
                "importance_score": r[3], "privacy_mode": r[4], "aging_stage": r[5],
                "recall_count": r[6], "timestamp": r[7], "is_pinned": bool(r[8] if len(r) > 8 else False)
            }
            if row_dict["is_pinned"]:
                total_pinned += 1
            
            ts = parse_sqlite_timestamp(row_dict["timestamp"])
            if ts < cutoff_date:
                total_scanned += 1
                if row_dict["is_pinned"]:
                    total_skipped_due_to_pinning += 1
                    total_retained += 1
                elif should_keep_memory(row_dict):
                    total_retained += 1
                else:
                    to_delete.append(row_dict)
            else:
                total_retained += 1

        # Consolidation of to_delete memories before actual deletion
        consolidated_new_count = 0
        if to_delete and not actual_dry_run:
            by_cat = {}
            for item in to_delete:
                cat = item["category"]
                if cat not in by_cat:
                    by_cat[cat] = []
                by_cat[cat].append(item)
                
            for cat, items in by_cat.items():
                summary_lines = [f"Consolidated stale {cat} memories:"]
                for it in items:
                    t_str = str(it["timestamp"])[:16]
                    summary_lines.append(f"- [{t_str}] {it['content']}")
                raw_text = "\n".join(summary_lines)
                
                summary_text = None
                # Check LLM availability
                gemini_key = get_gemini_key()
                if gemini_key and os.environ.get("NEXUS_TEST_MODE") != "1":
                    try:
                        from or_client import client
                        prompt = (
                            f"You are the NEXUS Memory Retention Engine. The following low-importance, older memories "
                            f"of category '{cat}' are being cleaned up. Consolidate them into a single concise "
                            f"bulleted or paragraph summary that captures their core essence so we don't lose the history.\n\n"
                            f"Memories:\n{raw_text}\n\n"
                            f"Consolidated Summary:"
                        )
                        summary_text = client.chat(prompt, system="Be extremely concise and direct. Return only the summary text.", max_tokens=300)
                        summary_text = summary_text.strip()
                    except Exception as e:
                        print(f"[MemoryEngine] Offline fallback: LLM consolidation failed: {e}")
                
                if not summary_text:
                    summary_text = raw_text
                
                max_importance = max(it["importance_score"] for it in items)
                privacy = 'PUBLIC'
                if any(it["privacy_mode"] == 'SYSTEM' for it in items):
                    privacy = 'SYSTEM'
                if any(it["privacy_mode"] == 'PRIVATE' for it in items):
                    privacy = 'PRIVATE'
                    
                new_id = self.add_semantic_memory(
                    category=cat,
                    content=summary_text,
                    importance_score=max(4, max_importance),
                    privacy_mode=privacy,
                    aging_stage='SHORT_TERM'
                )
                
                if new_id >= 0:
                    consolidated_new_count += 1
                    with mm._db_lock:
                        try:
                            conn = sqlite3.connect(str(mm.DB_PATH))
                            cursor = conn.cursor()
                            cursor.execute("UPDATE semantic_metadata SET is_consolidated = 1 WHERE faiss_id = ?", (new_id,))
                            conn.commit()
                            conn.close()
                        except Exception as e:
                            print(f"[MemoryEngine] Failed to set is_consolidated for new memory {new_id}: {e}")

        deleted_ids = [item["faiss_id"] for item in to_delete]
        
        # Execute deletions if not dry run
        if deleted_ids and not actual_dry_run:
            with mm._db_lock:
                try:
                    conn = sqlite3.connect(str(mm.DB_PATH))
                    cursor = conn.cursor()
                    placeholders = ",".join("?" for _ in deleted_ids)
                    cursor.execute(f"DELETE FROM semantic_metadata WHERE faiss_id IN ({placeholders})", deleted_ids)
                    conn.commit()
                    conn.close()
                except Exception as e:
                    print(f"[MemoryEngine] SQLite delete failed: {e}")
                    
            # Incremental FAISS removal
            if mm.check_embeddings_available():
                try:
                    import faiss
                    if mm._faiss_index is None:
                        mm.init_faiss()
                    if mm._faiss_index is not None:
                        mm._faiss_index = mm.ensure_id_map_index(mm._faiss_index)
                        mm._faiss_index.remove_ids(np.array(deleted_ids, dtype='int64'))
                        mm.save_faiss()
                        deleted_since_rebuild += len(deleted_ids)
                except Exception as e:
                    print(f"[MemoryEngine] FAISS incremental remove failed: {e}")

        # Enforce max database size
        pruned_count = 0
        if max_size and not actual_dry_run:
            db_stats = self.get_database_size_stats()
            total_size = db_stats["total_size_bytes"]
            
            if total_size > max_size:
                with mm._db_lock:
                    try:
                        conn = sqlite3.connect(str(mm.DB_PATH))
                        cursor = conn.cursor()
                        cursor.execute("SELECT faiss_id, category, content, importance_score, privacy_mode, aging_stage, recall_count, timestamp FROM semantic_metadata")
                        remaining_rows = cursor.fetchall()
                        
                        rem_memories = []
                        for r in remaining_rows:
                            rem_memories.append({
                                "faiss_id": r[0], "category": r[1], "content": r[2],
                                "importance_score": r[3], "privacy_mode": r[4], "aging_stage": r[5],
                                "recall_count": r[6], "timestamp": r[7]
                            })
                            
                        prunable = [m for m in rem_memories if not should_keep_memory(m)]
                        prunable.sort(key=lambda x: (x["importance_score"], x["timestamp"]))
                        
                        pruned_ids = []
                        for item in prunable:
                            if total_size <= max_size:
                                break
                            fid = item["faiss_id"]
                            cursor.execute("DELETE FROM semantic_metadata WHERE faiss_id = ?", (fid,))
                            pruned_ids.append(fid)
                            pruned_count += 1
                            
                            if len(pruned_ids) % 10 == 0:
                                conn.commit()
                                cursor.execute("VACUUM")
                                total_size = self.get_database_size_stats()["total_size_bytes"]
                                
                        conn.commit()
                        conn.close()
                        
                        if pruned_ids and mm.check_embeddings_available():
                            if mm._faiss_index is not None:
                                mm._faiss_index = mm.ensure_id_map_index(mm._faiss_index)
                                mm._faiss_index.remove_ids(np.array(pruned_ids, dtype='int64'))
                                mm.save_faiss()
                                deleted_since_rebuild += len(pruned_ids)
                    except Exception as e:
                        print(f"[MemoryEngine] Max size enforcement failed: {e}")

        # Vacuum SQLite database to reclaim space
        if not actual_dry_run:
            with mm._db_lock:
                try:
                    conn = sqlite3.connect(str(mm.DB_PATH))
                    conn.execute("VACUUM")
                    conn.close()
                except Exception as e:
                    print(f"[MemoryEngine] SQLite VACUUM failed: {e}")

        # Evaluate if full FAISS rebuild is needed
        rebuilt_faiss = False
        if mm.check_embeddings_available() and not actual_dry_run:
            try:
                import faiss
                if mm._faiss_index is None:
                    mm.init_faiss()
                if mm._faiss_index is not None:
                    mm._faiss_index = mm.ensure_id_map_index(mm._faiss_index)
                    total_vectors = mm._faiss_index.ntotal
                    total_before = total_vectors + deleted_since_rebuild
                    
                    frag_ratio = 0.0
                    if total_before > 0:
                        frag_ratio = deleted_since_rebuild / total_before
                        
                    if frag_ratio >= frag_threshold or force_rebuild:
                        print(f"[MemoryEngine] FAISS fragmentation ({frag_ratio:.2%}) exceeds threshold ({frag_threshold:.2%}). Rebuilding index...")
                        with mm._db_lock:
                            conn = sqlite3.connect(str(mm.DB_PATH))
                            cursor = conn.cursor()
                            cursor.execute("SELECT faiss_id, content FROM semantic_metadata")
                            db_records = cursor.fetchall()
                            conn.close()
                            
                        new_index = faiss.IndexIDMap(faiss.IndexFlatL2(384))
                        if db_records:
                            model = mm.get_embedding_model()
                            if model is not None:
                                contents = [rec[1] for rec in db_records]
                                ids = [rec[0] for rec in db_records]
                                embs = model.encode(contents).astype('float32')
                                new_index.add_with_ids(embs, np.array(ids, dtype='int64'))
                                
                        mm._faiss_index = new_index
                        mm.save_faiss()
                        deleted_since_rebuild = 0
                        rebuilt_faiss = True
                        print(f"[MemoryEngine] FAISS index successfully rebuilt with {mm._faiss_index.ntotal} vectors.")
            except Exception as e:
                print(f"[MemoryEngine] FAISS rebuild check failed: {e}")

        stats_after = self.get_database_size_stats()
        space_reclaimed = max(0, stats_before["total_size_bytes"] - stats_after["total_size_bytes"])
        
        faiss_status = "NOT_AVAILABLE"
        if mm.check_embeddings_available():
            if mm._faiss_index is not None:
                faiss_status = f"CONNECTED (vectors: {mm._faiss_index.ntotal}, mapped)"
            else:
                faiss_status = "INITIALIZATION_FAILED"
                
        report = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "dry_run": actual_dry_run,
            "retention_period_days": retention_days,
            "memories_scanned": total_scanned,
            "memories_retained": total_retained,
            "memories_consolidated": len(to_delete),
            "memories_deleted": len(deleted_ids) + pruned_count,
            "total_pinned_memories": total_pinned,
            "total_skipped_due_to_pinning": total_skipped_due_to_pinning,
            "space_reclaimed_bytes": space_reclaimed,
            "database_size_before_bytes": stats_before["total_size_bytes"],
            "database_size_after_bytes": stats_after["total_size_bytes"],
            "faiss_index_status": faiss_status,
            "faiss_index_rebuilt": rebuilt_faiss,
            "deleted_count_since_last_rebuild": deleted_since_rebuild
        }
        
        config["deleted_count_since_last_rebuild"] = deleted_since_rebuild
        self.save_cleanup_config(config)
        
        if not actual_dry_run:
            try:
                report_file = Path(__file__).resolve().parent / "cleanup_report.json"
                with open(report_file, "w", encoding="utf-8") as f:
                    json.dump(report, f, indent=2)
                    
                report_md = Path(__file__).resolve().parent / "cleanup_report.md"
                md_content = f"""# NEXUS Memory Engine Cleanup Report

**Execution Timestamp**: {report['timestamp']}
**Dry Run**: {report['dry_run']}
**Retention Configured**: {report['retention_period_days']} days

## Subsystem Statistics

| Metric | Before Cleanup | After Cleanup | Difference / Details |
|---|---|---|---|
| **Total Database Size** | {report['database_size_before_bytes'] / 1024:.2f} KB | {report['database_size_after_bytes'] / 1024:.2f} KB | **{report['space_reclaimed_bytes'] / 1024:.2f} KB Reclaimed** |
| **Memories Scanned** | — | {report['memories_scanned']} | Older than cutoff date |
| **Memories Retained** | — | {report['memories_retained']} | Met indefinite retention criteria |
| **Memories Consolidated** | — | {report['memories_consolidated']} | Low-importance raw items summarized |
| **Memories Deleted** | — | {report['memories_deleted']} | Expired & pruned database records |
| **Total Pinned Memories** | — | {report['total_pinned_memories']} | Explicitly pinned memories kept indefinitely |
| **Skipped due to Pinning** | — | {report['total_skipped_due_to_pinning']} | Stale memories preserved by pinning |
| **FAISS Index Status** | — | — | `{report['faiss_index_status']}` |
| **FAISS Index Rebuilt** | — | — | `{report['faiss_index_rebuilt']}` |
"""
                with open(report_md, "w", encoding="utf-8") as f:
                    f.write(md_content)
            except Exception as e:
                print(f"[MemoryEngine] Failed to write report files: {e}")
                
        return report
