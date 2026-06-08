# scratch/test_planner_integration.py
import sys
import os
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

print("Project root path:", project_root)

# Check imports
try:
    from agent.executor import AgentExecutor
    print("SUCCESS: Imported AgentExecutor!")
except Exception as e:
    print("FAILURE: Failed to import AgentExecutor:", e)
    sys.exit(1)

# Initialize AgentExecutor
executor = AgentExecutor()

# Define the multi-step goal
goal = (
    "Create a folder named NEXUS_PLANNER_TEST on my Desktop. "
    "Inside it, write a text file named integration.txt containing 'Hello World'. "
    "Edit integration.txt by replacing 'World' with 'Planner Integration'. "
    "Validate that integration.txt exists and return its size."
)

print(f"\n🎯 Running AgentExecutor on goal:\n{goal}\n")

try:
    result = executor.execute(goal)
    print("\n================ EXECUTOR SUMMARY ================")
    print(result)
    print("==================================================")
except Exception as e:
    print("\n❌ Executor failed:", e)
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Clean up planner test files
test_folder = Path.home() / "Desktop" / "NEXUS_PLANNER_TEST"
if test_folder.exists():
    try:
        import shutil
        shutil.rmtree(test_folder)
        print("Cleaned up NEXUS_PLANNER_TEST directory.")
    except Exception as e:
        print("Cleanup warning:", e)
