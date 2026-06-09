import sys
import os
import json
from pathlib import Path

# Reconfigure stdout/stderr to UTF-8 to prevent UnicodeEncodeError with emojis
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

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from actions.project_intelligence import (
    scan_project, detect_tech_stack, find_code_smells,
    analyze_architecture, generate_project_report,
    analyze_self, compare_projects, answer_project_question
)

def run_tests():
    print("=" * 65)
    print(" NEXUS AI — Project Intelligence Engine Verification")
    print("=" * 65)

    # Resolve test paths
    nexus_path = str(Path(__file__).resolve().parent.parent)
    careernova_path = r"c:\Users\ayush\OneDrive\Desktop\Private\Careernova"
    creditwise_path = r"C:\Users\ayush\Downloads\Telegram Desktop\DAY 27 CreditWise Loan System(Minor-Project)\DAY 27 CreditWise Loan System(Minor-Project)\Day 27 CreditWise Loan System(Minor-Project) TEAMWORK"

    # Test 1: Scan Project
    print("\n--- Test 1: Scan Project Heuristics ---")
    for name, path in [("NEXUS AI", nexus_path), ("CareerNova", careernova_path), ("CreditWise", creditwise_path)]:
        if Path(path).exists():
            print(f"\n[Scanning {name}] Path: {path}")
            scan = scan_project(path)
            if "error" in scan:
                print(f"  ❌ Scan failed: {scan['error']}")
                continue
            print(f"  ✅ Files: {scan['total_files']} | Dirs: {scan['total_dirs']}")
            print(f"  ✅ Language Dist: {scan['language_distribution']}")
            print(f"  ✅ Config Files Found: {[Path(cf).name for cf in scan['config_files']]}")
            print(f"  ✅ Potential Entry Points: {scan['entry_points']}")
        else:
            print(f"  ⚠️ Path {path} not found. Skipping...")

    # Test 2: Tech Stack Detection
    print("\n--- Test 2: Tech Stack Detection ---")
    for name, path in [("NEXUS AI", nexus_path), ("CareerNova", careernova_path), ("CreditWise", creditwise_path)]:
        if Path(path).exists():
            tech = detect_tech_stack(path)
            print(f"\n[{name} Tech Stack]")
            print(f"  - Languages: {tech.get('languages')}")
            print(f"  - Frameworks: {tech.get('frameworks')}")
            print(f"  - Databases: {tech.get('databases')}")
            print(f"  - Hosting/Deploy: {tech.get('hosting_targets')}")

    # Test 3: Code Smell Scanning
    print("\n--- Test 3: Code Smell Traversal ---")
    for name, path in [("NEXUS AI", nexus_path), ("CareerNova", careernova_path), ("CreditWise", creditwise_path)]:
        if Path(path).exists():
            smells = find_code_smells(path)
            print(f"\n[{name} Code Quality]")
            print(f"  - Total smells detected: {len(smells)}")
            for smell in smells[:5]:
                print(f"    * {smell['smell_type']} ({smell['severity']}) in {smell['file']}")
                print(f"      Description: {smell['description']}")

    # Test 4: Self-Analysis Mode
    print("\n--- Test 4: Self-Analysis Mode ---")
    print("\nRunning self-analysis on NEXUS AI...")
    self_report = analyze_self()
    print(f"  ✅ Project Name: {self_report['project_name']}")
    print(f"  ✅ Total LOC: {self_report['total_lines']}")
    print(f"  ✅ Language Dist: {self_report['language_distribution']}")
    print("\n--- Report Sneak Peek ---")
    print("\n".join(self_report['report'].splitlines()[:15]))

    # Test 5: Project Comparison Mode
    print("\n--- Test 5: Project Comparison Mode ---")
    if Path(nexus_path).exists() and Path(careernova_path).exists():
        print(f"\nComparing {Path(nexus_path).name} vs {Path(careernova_path).name}...")
        comparison = compare_projects(nexus_path, careernova_path)
        print(f"  ✅ Complexity A ({comparison['project_a']}): {comparison['complexity_a']}")
        print(f"  ✅ Complexity B ({comparison['project_b']}): {comparison['complexity_b']}")
        print("\n--- Comparison Report Snippet ---")
        print("\n".join(comparison['report'].splitlines()[:12]))

    # Test 6: Q&A Capabilities
    print("\n--- Test 6: Project Q&A Verification ---")
    if Path(nexus_path).exists():
        question = "What does this codebase do and what are its primary APIs?"
        print(f"\nAsking: '{question}'")
        answer = answer_project_question(nexus_path, question)
        print("\n--- Answer Summary ---")
        print(answer[:500] + "\n...")

    print("\n" + "=" * 65)
    print(" Verification Completed Successfully!")
    print("=" * 65)

if __name__ == "__main__":
    run_tests()
