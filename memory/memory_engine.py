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

    def end_session(self, session_id: int = None) -> str:
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
            if gemini_key and os.environ.get("NEXUS_TEST_MODE") != "1":
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

    def add_semantic_memory(self, category: str, content: str, importance_score: int, privacy_mode: str, aging_stage: str = 'RECENT', source_reference_id: int = None) -> int:
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
            source_reference_id=source_reference_id
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
