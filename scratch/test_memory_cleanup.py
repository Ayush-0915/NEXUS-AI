import os
import sys
import time
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Use test environment and override paths
os.environ["NEXUS_TEST_MODE"] = "1"
import memory.memory_manager as mm
import memory.memory_engine as me

# Safely redirect test DB and FAISS paths
mm.DB_PATH = mm.BASE_DIR / "memory" / "test_nexus_memory.db"
mm.FAISS_INDEX_PATH = mm.BASE_DIR / "memory" / "test_nexus_vectors.faiss"
me.DB_PATH = mm.DB_PATH
me._db_lock = mm._db_lock

# Remove any old test database files
for p in (mm.DB_PATH, mm.FAISS_INDEX_PATH):
    if p.exists():
        try:
            os.remove(p)
        except Exception:
            pass

# Initialize fresh databases
mm.init_db()
mm.init_faiss()

from memory.memory_engine import MemoryEngine

def run_retention_tests():
    print("==========================================================")
    print("[RUNNING] NEXUS MEMORY RETENTION AND CLEANUP TEST SUITE")
    print("==========================================================\n")
    
    engine = MemoryEngine()
    
    # Configure test settings
    config = engine.load_cleanup_config()
    config["retention_period_days"] = 30
    config["automatic_cleanup_enabled"] = True
    config["max_database_size_bytes"] = 10 * 1024 * 1024  # 10MB limit
    config["dry_run"] = False
    config["fragmentation_threshold"] = 0.15
    engine.save_cleanup_config(config)

    # Base dates
    now = datetime.now()
    old_date = (now - timedelta(days=35)).strftime("%Y-%m-%d %H:%M:%S")
    recent_date = (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    
    # ----------------------------------------------------
    # Step 1: Populate database with diverse memories
    # ----------------------------------------------------
    print("[1] Inserting test memories...")
    
    # Indefinite: High importance (>= 7)
    id1 = engine.add_semantic_memory("DEVELOPMENT", "Important server deploy guidelines.", 8, "PUBLIC")
    
    # Indefinite: Architecture decision
    id2 = engine.add_semantic_memory("ARCHITECTURE", "Decided to use standard SQlite thread locks.", 5, "PUBLIC")
    
    # Indefinite: Project milestone (FEATURE/PROJECT with importance >= 8)
    id3 = engine.add_semantic_memory("FEATURE", "Milestone 1 completed successfully.", 9, "PUBLIC")
    
    # Indefinite: User preference (keywords)
    id4 = engine.add_semantic_memory("CHAT", "User's favorite programming language is Python.", 4, "PUBLIC")
    
    # Indefinite: Critical bug fix (BUG with importance >= 7)
    id5 = engine.add_semantic_memory("BUG", "Fixed critical database thread contention lock error.", 7, "PUBLIC")
    
    # Indefinite: Frequently recalled (recall_count >= 3)
    id6 = engine.add_semantic_memory("CHAT", "A normal conversation memory that got recalled many times.", 4, "PUBLIC")
    
    # Eligible for cleanup: Stale low-importance memory (category CHAT, importance 3, recall_count 0)
    id7 = engine.add_semantic_memory("CHAT", "User talked about breakfast details.", 3, "PUBLIC")
    
    # Eligible for cleanup: Another stale low-importance memory
    id8 = engine.add_semantic_memory("DEVELOPMENT", "Fixed minor alignment gap on styling helper class.", 2, "PUBLIC")
    
    # Newer memory (within 30 days - must keep)
    id9 = engine.add_semantic_memory("CHAT", "Recent conversation about NEXUS plans.", 3, "PUBLIC")
    
    # Indefinite: Explicitly Pinned (is_pinned=True, category=CHAT, importance=3, older than 30 days)
    id10 = engine.add_semantic_memory("CHAT", "Manually pinned user API config key note.", 3, "PUBLIC", is_pinned=True)

    # Override dates in database to simulate age
    conn = sqlite3.connect(str(mm.DB_PATH))
    cursor = conn.cursor()
    
    # Mark old_date for ids 1-8 and 10
    old_ids = (id1, id2, id3, id4, id5, id6, id7, id8, id10)
    placeholders = ",".join("?" for _ in old_ids)
    cursor.execute(f"UPDATE semantic_metadata SET timestamp = ? WHERE faiss_id IN ({placeholders})", (old_date,) + old_ids)
    
    # Mark recent_date for id9
    cursor.execute("UPDATE semantic_metadata SET timestamp = ? WHERE faiss_id = ?", (recent_date, id9))
    
    # Set recall count >= 3 for id6
    cursor.execute("UPDATE semantic_metadata SET recall_count = 5 WHERE faiss_id = ?", (id6,))
    
    conn.commit()
    
    # Check count before cleanup
    cursor.execute("SELECT COUNT(*) FROM semantic_metadata")
    count_before = cursor.fetchone()[0]
    print(f"  * Total memories before cleanup: {count_before}")
    assert count_before == 10, f"Expected 10 memories, got {count_before}"
    
    # Retrieve FAISS count before cleanup
    vectors_before = mm._faiss_index.ntotal if mm._faiss_index else 0
    print(f"  * Total vectors in FAISS before cleanup: {vectors_before}")
    
    conn.close()
    
    # ----------------------------------------------------
    # Step 2: Run dry-run cleanup cycle
    # ----------------------------------------------------
    print("\n[2] Running cleanup in DRY-RUN mode...")
    report_dry = engine.run_memory_cleanup(dry_run=True)
    print(f"  * Dry-run report: Scanned: {report_dry['memories_scanned']}, Deleted (Estimated): {report_dry['memories_deleted']}")
    assert report_dry['memories_deleted'] == 2, f"Expected 2 eligible items to delete in dry run, got {report_dry['memories_deleted']}"
    
    # Verify DB count hasn't changed
    conn = sqlite3.connect(str(mm.DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM semantic_metadata")
    assert cursor.fetchone()[0] == 10, "Dry-run modified the database!"
    conn.close()
    print("  * Dry-run verified: database count unaffected.")

    # ----------------------------------------------------
    # Step 3: Run active cleanup cycle (Incremental mode)
    # ----------------------------------------------------
    print("\n[3] Running active cleanup (incremental)...")
    report_active = engine.run_memory_cleanup(dry_run=False)
    print(f"  * Active cleanup report: Scanned: {report_active['memories_scanned']}, Consolidated: {report_active['memories_consolidated']}, Deleted: {report_active['memories_deleted']}")
    print(f"  * Pinned count in DB: {report_active['total_pinned_memories']}")
    print(f"  * Skipped due to pinning: {report_active['total_skipped_due_to_pinning']}")
    print(f"  * Space Reclaimed: {report_active['space_reclaimed_bytes'] / 1024:.2f} KB")
    
    assert report_active['total_pinned_memories'] == 1, f"Expected 1 pinned memory, got {report_active['total_pinned_memories']}"
    assert report_active['total_skipped_due_to_pinning'] == 1, f"Expected 1 skipped due to pinning, got {report_active['total_skipped_due_to_pinning']}"

    # ----------------------------------------------------
    # Step 4: Verify Database State after cleanup
    # ----------------------------------------------------
    print("\n[4] Verifying SQLite database state...")
    conn = sqlite3.connect(str(mm.DB_PATH))
    cursor = conn.cursor()
    
    # We started with 10.
    # 2 (id7, id8) should have been deleted.
    # New consolidated summary memories (for categories CHAT and DEVELOPMENT) should be inserted.
    # Total remaining should be: 10 - 2 (deleted) + 2 (consolidated) = 10.
    cursor.execute("SELECT faiss_id, category, content, importance_score, timestamp, is_consolidated, is_pinned FROM semantic_metadata")
    remaining_records = cursor.fetchall()
    
    print(f"  * Total memories after cleanup: {len(remaining_records)}")
    
    remaining_ids = [r[0] for r in remaining_records]
    print(f"  * Remaining SQLite IDs: {remaining_ids}")
    
    # Ensure raw stale records (id7, id8) are deleted
    assert id7 not in remaining_ids, "Stale memory id7 was not deleted!"
    assert id8 not in remaining_ids, "Stale memory id8 was not deleted!"
    
    # Ensure retained records exist
    assert id1 in remaining_ids, "High-importance memory was incorrectly deleted!"
    assert id2 in remaining_ids, "Architecture decision was incorrectly deleted!"
    assert id3 in remaining_ids, "Milestone was incorrectly deleted!"
    assert id4 in remaining_ids, "Preference was incorrectly deleted!"
    assert id5 in remaining_ids, "Critical bug fix was incorrectly deleted!"
    assert id6 in remaining_ids, "Recalled memory was incorrectly deleted!"
    assert id9 in remaining_ids, "New memory was incorrectly deleted!"
    assert id10 in remaining_ids, "Pinned memory was incorrectly deleted!"
    
    # Find consolidated memories
    cursor.execute("SELECT content FROM semantic_metadata WHERE is_consolidated = 1")
    consolidated_texts = [r[0] for r in cursor.fetchall()]
    print(f"  * Consolidated memories created: {len(consolidated_texts)}")
    for t in consolidated_texts:
        print(f"    - Content: {t.splitlines()[0]}...")
    assert len(consolidated_texts) >= 2, "Consolidated summaries were not created!"
    
    conn.close()

    # ----------------------------------------------------
    # Step 5: Test pin and unpin operations explicitly
    # ----------------------------------------------------
    print("\n[5] Testing explicit pin/unpin operations...")
    # Unpin id10
    unpin_ok = engine.unpin_memory(id10)
    assert unpin_ok is True, "Failed to unpin memory"
    
    conn = sqlite3.connect(str(mm.DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT is_pinned FROM semantic_metadata WHERE faiss_id = ?", (id10,))
    assert cursor.fetchone()[0] == 0, "Memory was not unpinned in SQLite!"
    conn.close()
    
    # Pin id10 again
    pin_ok = engine.pin_memory(id10)
    assert pin_ok is True, "Failed to pin memory"
    
    conn = sqlite3.connect(str(mm.DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT is_pinned FROM semantic_metadata WHERE faiss_id = ?", (id10,))
    assert cursor.fetchone()[0] == 1, "Memory was not pinned in SQLite!"
    conn.close()
    print("  * Explicit pin/unpin checks: PASSED.")

    # ----------------------------------------------------
    # Step 6: Verify FAISS Index state & integrity
    # ----------------------------------------------------
    print("\n[6] Verifying FAISS vector index status & integrity...")
    if mm.check_embeddings_available() and mm._faiss_index:
        faiss_count = mm._faiss_index.ntotal
        print(f"  * FAISS total vectors: {faiss_count}")
        search_res = engine.search_semantic_memory("database thread lock", limit=2)
        print("  * Semantic search results for 'database thread lock':")
        for idx, res in enumerate(search_res):
            print(f"    {idx+1}. ID={res['faiss_id']} (Imp={res['importance_score']}) [{res['category']}]: {res['content']}")
            
        assert len(search_res) > 0, "Semantic search failed after cleanup!"
        best_id = search_res[0]["faiss_id"]
        assert best_id in (id5, id2), f"Expected search to find critical bug fix or decision, got ID {best_id}"
        
        # Search for pinned memory content
        search_pinned = engine.search_semantic_memory("Manually pinned user API config key note", limit=1)
        assert len(search_pinned) > 0, "Failed to search and find pinned memory"
        assert search_pinned[0]["faiss_id"] == id10, "Incorrect memory returned for pinned query"
        assert search_pinned[0]["is_pinned"] is True, "is_pinned flag not returned as True in search result!"
        print("  * Semantic search accuracy, integrity, and pinned status returned: PASSED.")
    else:
        print("  * FAISS vector index not active/available for search test.")

    # ----------------------------------------------------
    # Step 7: Test fragmentation rebuild trigger
    # ----------------------------------------------------
    print("\n[7] Testing fragmentation rebuild threshold trigger...")
    report_rebuild = engine.run_memory_cleanup(force_rebuild=True)
    print(f"  * Rebuilt FAISS Index: {report_rebuild['faiss_index_rebuilt']}")
    assert report_rebuild['faiss_index_rebuilt'] is True, "FAISS index was not rebuilt on forced trigger!"
    print("  * FAISS rebuild synchronization verified.")
    
    print("\n==========================================================")
    print("[SUCCESS] ALL MEMORY CLEANUP TESTS PASSED SUCCESSFULLY!")
    print("==========================================================\n")

if __name__ == "__main__":
    run_retention_tests()
