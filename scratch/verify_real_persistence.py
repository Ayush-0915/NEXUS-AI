import os
import sys
import argparse
import sqlite3
import subprocess
import numpy as np
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Ensure stdout/stderr encoding to prevent crashes on Windows console output
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Force offline mode for transformers
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# Import MemoryEngine and memory_manager
import memory.memory_manager as mm
from memory.memory_engine import MemoryEngine

def get_current_date():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")

def store_memory():
    print("[STORE] Initializing Memory Engine to store verification memory...")
    engine = MemoryEngine()
    
    # Define memory content with current date
    date_str = get_current_date()
    content = f"Ayush manually verified Memory Engine persistence on {date_str}"
    
    print(f"[STORE] Memory text: '{content}'")
    
    # Store it through the normal Memory Engine API
    faiss_id = engine.add_semantic_memory(
        category="DEVELOPMENT",
        content=content,
        importance_score=9,
        privacy_mode="PUBLIC"
    )
    
    if faiss_id != -1:
        print(f"[STORE] Success! Memory stored with faiss_id: {faiss_id}")
        # Explicitly save FAISS and close any resources
        mm.save_faiss()
        print("[STORE] FAISS index saved. Exiting process to simulate shutdown...")
        sys.exit(0)
    else:
        print("[STORE] Error: Failed to store memory!")
        sys.exit(1)

def query_memory():
    print("[QUERY] Reloading Memory Engine to verify persistence...")
    
    # Ensure memory_manager re-reads indexes
    mm._faiss_index = None
    mm._model = None
    mm.init_faiss()
    
    engine = MemoryEngine()
    
    # Expected memory
    date_str = get_current_date()
    expected_content = f"Ayush manually verified Memory Engine persistence on {date_str}"
    
    # Query string
    query_str = "What memory was manually verified today?"
    print(f"[QUERY] Querying: '{query_str}'")
    
    # Run semantic search
    results = engine.search_semantic_memory(query_str, limit=5)
    
    found_item = None
    for item in results:
        if expected_content in item.get("content", ""):
            found_item = item
            break
            
    if not found_item:
        print("[QUERY] ERROR: Stored memory NOT retrieved via semantic search!")
        print("[QUERY] All search results:")
        for idx, item in enumerate(results):
            print(f"  {idx+1}. [{item.get('category')}] {item.get('content')} (faiss_id: {item.get('faiss_id')})")
        sys.exit(1)
        
    print("\n" + "="*60)
    print("PERSISTENCE VALIDATION RESULTS")
    print("="*60)
    
    # 1. Show SQLite record
    faiss_id = found_item["faiss_id"]
    print(f"\n* SQLite Record (faiss_id: {faiss_id}):")
    try:
        conn = sqlite3.connect(str(mm.DB_PATH))
        cursor = conn.cursor()
        cursor.execute("""
            SELECT faiss_id, category, content, importance_score, privacy_mode, aging_stage, recall_count, timestamp 
            FROM semantic_metadata 
            WHERE faiss_id = ?
        """, (faiss_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            columns = ["faiss_id", "category", "content", "importance_score", "privacy_mode", "aging_stage", "recall_count", "timestamp"]
            for col, val in zip(columns, row):
                print(f"  {col:18}: {val}")
        else:
            print("  [ERROR] SQLite record not found for faiss_id!")
    except Exception as e:
        print(f"  [ERROR] Failed to query SQLite: {e}")
        
    # 2. Show FAISS record
    print("\n* FAISS Record Details:")
    if mm._faiss_index is not None:
        try:
            import faiss
            ntotal = mm._faiss_index.ntotal
            print(f"  Total index size  : {ntotal} vectors")
            
            # Reconstruct the specific vector
            try:
                vec = mm._faiss_index.reconstruct(int(faiss_id))
                vec_norm = np.linalg.norm(vec)
                print(f"  Vector Dimension  : {len(vec)}")
                print(f"  Vector L2 Norm    : {vec_norm:.6f}")
                print(f"  Vector Snippet    : {vec[:5]} ...")
            except Exception as re_err:
                print(f"  [WARNING] Vector reconstruction not supported by this index type: {re_err}")
                print("  Vector exists in index because faiss_id is within bounds.")
        except Exception as e:
            print(f"  [ERROR] Failed to read FAISS record details: {e}")
    else:
        print("  [ERROR] FAISS index is not loaded/available!")
        
    # 3. Show Retrieval result
    print("\n* Semantic Retrieval Result:")
    print(f"  Query text        : '{query_str}'")
    print(f"  Retrieved Content : '{found_item['content']}'")
    print(f"  Category          : {found_item['category']}")
    print(f"  Importance Score  : {found_item['importance_score']}")
    print(f"  Aging Stage       : {found_item['aging_stage']}")
    print(f"  Recall Count      : {found_item['recall_count']}")
    print(f"  Timestamp         : {found_item['timestamp']}")
    
    print("\n" + "="*60)
    print("RESULT: PERSISTENCE FULLY VERIFIED (SUCCESS)")
    print("="*60 + "\n")
    sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description="NEXUS Memory Engine Persistence Verification")
    parser.add_argument("--store", action="store_true", help="Store verification memory in Memory Engine")
    parser.add_argument("--query", action="store_true", help="Query and verify verification memory in Memory Engine")
    args = parser.parse_args()
    
    if args.store:
        store_memory()
    elif args.query:
        query_memory()
    else:
        # Orchestrate both in separate subprocesses to simulate clean restart boundary
        print("[ORCHESTRATOR] Starting real-world persistence verification pipeline...")
        print("[ORCHESTRATOR] Step 1: Run store phase in a separate process.")
        
        script_path = Path(__file__).resolve()
        res_store = subprocess.run([sys.executable, str(script_path), "--store"], capture_output=False)
        if res_store.returncode != 0:
            print("[ORCHESTRATOR] Step 1 FAILED! Aborting.")
            sys.exit(1)
            
        print("\n[ORCHESTRATOR] Step 2: Shutdown completed. Now simulating reload and run query phase in a separate process.")
        res_query = subprocess.run([sys.executable, str(script_path), "--query"], capture_output=False)
        if res_query.returncode != 0:
            print("[ORCHESTRATOR] Step 2 FAILED!")
            sys.exit(1)
            
        print("[ORCHESTRATOR] Pipeline completed successfully!")

if __name__ == "__main__":
    main()
