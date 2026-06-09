# actions/project_intelligence.py
import os
import re
import json
import sys
from pathlib import Path
from google import genai
from google.genai import types

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

def _get_api_key() -> str:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)["gemini_api_key"]
    except Exception:
        return os.environ.get("GEMINI_API_KEY", "")

def _get_gemini_client() -> genai.Client | None:
    api_key = _get_api_key()
    if not api_key:
        return None
    try:
        return genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
    except Exception:
        return None

# Ignored directories and files
IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv",
    ".next", "dist", "build", ".vercel", "bin", "obj", ".idea", ".vscode"
}
IGNORED_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "workspace_registry.json", "workspace_registry.json.bak"
}

def scan_project(path: str) -> dict:
    project_path = Path(path).resolve()
    if not project_path.exists() or not project_path.is_dir():
        return {"error": f"Path '{path}' does not exist or is not a directory."}

    file_stats = {
        "total_files": 0,
        "total_dirs": 0,
        "total_lines": 0,
        "total_size_bytes": 0,
        "extensions": {}
    }
    
    file_list = []
    config_files = []
    dependencies = []
    entry_points = []
    
    # 1. Recursive Scan
    for root, dirs, files in os.walk(project_path):
        # Prune ignored directories
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
        
        rel_dir = os.path.relpath(root, project_path)
        if rel_dir != ".":
            file_stats["total_dirs"] += 1
            
        for file in files:
            if file in IGNORED_FILES or file.startswith("."):
                continue
                
            file_path = Path(root) / file
            rel_file_path = file_path.relative_to(project_path)
            
            # File statistics
            try:
                size = file_path.stat().st_size
                file_stats["total_size_bytes"] += size
                file_stats["total_files"] += 1
                
                ext = file_path.suffix.lower()
                if not ext:
                    ext = "[no_ext]"
                file_stats["extensions"][ext] = file_stats["extensions"].get(ext, 0) + 1
                
                # Check for config files
                if file in ("package.json", "requirements.txt", "pyproject.toml", "setup.py",
                            "tsconfig.json", "vite.config.ts", "next.config.js", "tailwind.config.js",
                            "dockerfile", "Dockerfile", "docker-compose.yml", "vercel.json"):
                    config_files.append(str(rel_file_path))
                    
                # Identify potential entry points
                if file in ("main.py", "app.py", "index.js", "server.js", "index.html", "src/index.tsx", "manage.py"):
                    entry_points.append(str(rel_file_path))
                    
                # Count lines for text files
                if ext in (".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".c", ".cpp", ".h", ".hpp", ".java", ".json", ".md", ".txt"):
                    try:
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                            lines = f.readlines()
                            file_stats["total_lines"] += len(lines)
                    except Exception:
                        pass
                        
                file_list.append(rel_file_path)
            except Exception:
                pass

    # Sort extensions by count
    sorted_exts = sorted(file_stats["extensions"].items(), key=lambda x: x[1], reverse=True)
    lang_dist = {}
    total_ext_files = sum(c for _, c in sorted_exts) or 1
    for ext, count in sorted_exts:
        percentage = round((count / total_ext_files) * 100, 1)
        lang_name = _ext_to_lang(ext)
        lang_dist[lang_name] = lang_dist.get(lang_name, 0.0) + percentage
        
    for k, v in lang_dist.items():
        lang_dist[k] = round(v, 1)

    # 2. Dependency Extraction
    dependencies = _extract_dependencies(project_path)

    # 3. Generate Folder Tree (Depth limit 3)
    folder_tree = _generate_folder_tree(project_path)

    # 4. Generate Import Graph & Module Relationships
    import_graph = _generate_import_graph(project_path, file_list)

    return {
        "project_name": project_path.name,
        "project_path": str(project_path),
        "total_files": file_stats["total_files"],
        "total_dirs": file_stats["total_dirs"],
        "total_lines": file_stats["total_lines"],
        "total_size_bytes": file_stats["total_size_bytes"],
        "language_distribution": lang_dist,
        "config_files": config_files,
        "dependencies": dependencies,
        "entry_points": entry_points,
        "folder_tree": folder_tree,
        "import_graph": import_graph
    }

def _ext_to_lang(ext: str) -> str:
    mapping = {
        ".py": "Python",
        ".js": "JavaScript",
        ".jsx": "React JS",
        ".ts": "TypeScript",
        ".tsx": "React TS",
        ".html": "HTML",
        ".css": "CSS",
        ".c": "C",
        ".cpp": "C++",
        ".h": "C/C++ Header",
        ".hpp": "C/C++ Header",
        ".java": "Java",
        ".json": "JSON",
        ".md": "Markdown",
        ".txt": "Text",
        ".ipynb": "Jupyter Notebook",
        ".sh": "Shell Script",
        ".bat": "Batch Script",
        ".ps1": "PowerShell",
        ".yml": "YAML",
        ".yaml": "YAML",
        ".toml": "TOML",
        ".xml": "XML",
        "[no_ext]": "Binary/Text"
    }
    return mapping.get(ext, f"Other ({ext})")

def _extract_dependencies(project_path: Path) -> list:
    deps = []
    
    # Python requirements.txt
    req_path = project_path / "requirements.txt"
    if req_path.exists():
        try:
            with open(req_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        # strip versions
                        match = re.match(r"^([a-zA-Z0-9_\-]+)", line)
                        if match:
                            deps.append(match.group(1))
        except Exception:
            pass
            
    # Node package.json
    pkg_path = project_path / "package.json"
    if pkg_path.exists():
        try:
            with open(pkg_path, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
                if "dependencies" in data:
                    deps.extend(data["dependencies"].keys())
                if "devDependencies" in data:
                    deps.extend(data["devDependencies"].keys())
        except Exception:
            pass
            
    # Python pyproject.toml
    toml_path = project_path / "pyproject.toml"
    if toml_path.exists():
        try:
            with open(toml_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                # Simple regex dependencies finder
                matches = re.findall(r'dependencies\s*=\s*\[(.*?)\]', content, re.DOTALL)
                for match in matches:
                    items = re.findall(r'"([^"]+)"|\'([^\']+)\'', match)
                    for item in items:
                        dep_str = item[0] or item[1]
                        name_match = re.match(r"^([a-zA-Z0-9_\-]+)", dep_str)
                        if name_match:
                            deps.append(name_match.group(1))
        except Exception:
            pass
            
    return list(set(deps))

def _generate_folder_tree(project_path: Path, max_depth: int = 3) -> str:
    lines = []
    
    def recurse(curr_path: Path, prefix: str = "", depth: int = 1):
        if depth > max_depth:
            return
            
        try:
            items = sorted(curr_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except Exception:
            return
            
        items = [i for i in items if i.name not in IGNORED_DIRS and i.name not in IGNORED_FILES and not i.name.startswith(".")]
        
        for idx, item in enumerate(items):
            is_last = (idx == len(items) - 1)
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{item.name}")
            
            if item.is_dir():
                new_prefix = prefix + ("    " if is_last else "│   ")
                recurse(item, new_prefix, depth + 1)
                
    lines.append(project_path.name)
    recurse(project_path)
    return "\n".join(lines)

def _generate_import_graph(project_path: Path, file_list: list) -> dict:
    graph = {}
    py_files = [f for f in file_list if f.suffix == ".py"]
    js_files = [f for f in file_list if f.suffix in (".js", ".ts", ".jsx", ".tsx")]
    
    # 1. Python Imports
    for rel_file in py_files:
        full_path = project_path / rel_file
        module_name = rel_file.stem
        imports = []
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("import ") or line.startswith("from "):
                        # Regex for python imports
                        m1 = re.match(r"^import\s+([a-zA-Z0-9_\., ]+)", line)
                        m2 = re.match(r"^from\s+([a-zA-Z0-9_\.]+)\s+import", line)
                        if m1:
                            parts = [p.strip().split(" as ")[0].split(".")[0] for p in m1.group(1).split(",")]
                            imports.extend(parts)
                        elif m2:
                            imports.append(m2.group(1).split(".")[0])
        except Exception:
            pass
        if imports:
            graph[module_name] = list(set([imp for imp in imports if imp != module_name]))

    # 2. JS/TS Imports
    for rel_file in js_files:
        full_path = project_path / rel_file
        module_name = rel_file.stem
        imports = []
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    # ES6 imports: import X from './Y'
                    m1 = re.search(r"import\s+.*\s+from\s+['\"](.*?)['\"]", line)
                    # CommonJS: require('./Y')
                    m2 = re.search(r"require\(['\"](.*?)['\"]\)", line)
                    
                    target = None
                    if m1:
                        target = m1.group(1)
                    elif m2:
                        target = m2.group(1)
                        
                    if target:
                        # Extract basename or module name
                        basename = target.split("/")[-1].split(".")[0]
                        if basename:
                            imports.append(basename)
        except Exception:
            pass
        if imports:
            graph[module_name] = list(set([imp for imp in imports if imp != module_name]))
            
    return graph

def detect_tech_stack(path: str) -> dict:
    scan = scan_project(path)
    if "error" in scan:
        return scan
        
    langs = list(scan["language_distribution"].keys())
    frameworks = []
    libraries = []
    databases = []
    hosting = []
    
    deps = scan["dependencies"]
    config_names = [Path(c).name.lower() for c in scan["config_files"]]
    
    # Framework / Library Rules
    # JS/TS frameworks
    if "next" in deps:
        frameworks.append("Next.js")
    if "react" in deps:
        frameworks.append("React")
    if "vue" in deps:
        frameworks.append("Vue")
    if "express" in deps:
        frameworks.append("Express.js")
    if "react-native" in deps:
        frameworks.append("React Native")
        
    # Python frameworks
    if "fastapi" in deps or "fastapi" in scan["language_distribution"]:
        frameworks.append("FastAPI")
    if "flask" in deps:
        frameworks.append("Flask")
    if "django" in deps:
        frameworks.append("Django")
    if "streamlit" in deps:
        frameworks.append("Streamlit")
        
    # Databases
    if "prisma" in deps:
        libraries.append("Prisma ORM")
    if "sequelize" in deps:
        libraries.append("Sequelize ORM")
    if "sqlalchemy" in deps:
        libraries.append("SQLAlchemy")
    if "pg" in deps or "postgresql" in deps or "psycopg2" in deps:
        databases.append("PostgreSQL")
    if "mysql" in deps or "mysqlconnector" in deps or "mysqlclient" in deps:
        databases.append("MySQL")
    if "mongodb" in deps or "mongoose" in deps or "pymongo" in deps:
        databases.append("MongoDB")
    if "redis" in deps or "redis-py" in deps:
        databases.append("Redis")
    if "sqlite3" in deps or "sqlite" in deps:
        databases.append("SQLite")
    if "supabase" in deps or "supabase-py" in deps:
        databases.append("Supabase (Backend-as-a-Service)")
        
    # Hosting targets
    if "vercel.json" in config_names:
        hosting.append("Vercel")
    if "netlify.toml" in config_names:
        hosting.append("Netlify")
    if "dockerfile" in config_names or "docker-compose.yml" in config_names:
        hosting.append("Docker")
        
    # Fallback to file scan heuristic
    for file_name in scan["entry_points"]:
        if "manage.py" in file_name:
            if "Django" not in frameworks: frameworks.append("Django")
            
    # Clean duplicates and format
    return {
        "languages": langs,
        "frameworks": list(set(frameworks)),
        "libraries": list(set(libraries)),
        "databases": list(set(databases)),
        "hosting_targets": list(set(hosting))
    }

def find_code_smells(path: str) -> list:
    project_path = Path(path).resolve()
    smells = []
    
    # Recursively traverse
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
        
        for file in files:
            if file in IGNORED_FILES or file.startswith("."):
                continue
                
            file_path = Path(root) / file
            rel_path = file_path.relative_to(project_path)
            
            ext = file_path.suffix.lower()
            if ext not in (".py", ".js", ".ts", ".jsx", ".tsx"):
                continue
                
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    lines = content.splitlines()
                    
                # 1. Large File Smell (> 600 lines)
                if len(lines) > 600:
                    smells.append({
                        "file": str(rel_path),
                        "smell_type": "Large File",
                        "severity": "MEDIUM" if len(lines) < 1200 else "HIGH",
                        "description": f"File has {len(lines)} lines. Large files are difficult to maintain and understand."
                    })
                    
                # 2. Hardcoded Secrets Smell
                # Look for patterns like api_key = "..."
                secret_patterns = [
                    (r'(?i)(api_key|apikey|secret|password|db_pass|jwt_secret|private_key)\s*=\s*[\'"][a-zA-Z0-9_\-]{8,}[\'"]', "Hardcoded Secret"),
                    (r'(?i)(bearer|token|auth_token)\s*=\s*[\'"][a-zA-Z0-9_\-\.]{12,}[\'"]', "Hardcoded Token")
                ]
                for pattern, name in secret_patterns:
                    matches = re.finditer(pattern, content)
                    for match in matches:
                        # Ensure it's not in a comment
                        line_idx = content[:match.start()].count("\n")
                        line_str = lines[line_idx] if line_idx < len(lines) else ""
                        if not line_str.strip().startswith("#") and not line_str.strip().startswith("//"):
                            smells.append({
                                "file": str(rel_path),
                                "smell_type": name,
                                "severity": "HIGH",
                                "description": f"Potential hardcoded key/secret found on line {line_idx+1}: '{line_str.strip()[:40]}...'"
                            })
                            
                # 3. Excessive Nesting (> 5 tabs or 20 spaces)
                max_nesting = 0
                nesting_line = 0
                for idx, line in enumerate(lines):
                    leading_spaces = len(line) - len(line.lstrip(' '))
                    leading_tabs = len(line) - len(line.lstrip('\t'))
                    nest = leading_tabs + (leading_spaces // 4)
                    if nest > max_nesting:
                        max_nesting = nest
                        nesting_line = idx + 1
                        
                if max_nesting > 5:
                    smells.append({
                        "file": str(rel_path),
                        "smell_type": "Excessive Indentation",
                        "severity": "LOW" if max_nesting <= 6 else "MEDIUM",
                        "description": f"Nesting level of {max_nesting} detected at line {nesting_line}. Excessively nested code is hard to read."
                    })
                    
            except Exception:
                pass
                
    return smells

# --- Gemini Semantic Functions ---

def analyze_architecture(path: str) -> str:
    scan = scan_project(path)
    if "error" in scan:
        return scan["error"]
        
    tech = detect_tech_stack(path)
    
    prompt = f"""
Analyze the architecture of this software project based on the following local metrics:
Project Name: {scan['project_name']}
Languages: {json.dumps(scan['language_distribution'])}
Config Files: {json.dumps(scan['config_files'])}
Entry Points: {json.dumps(scan['entry_points'])}
Frameworks: {json.dumps(tech['frameworks'])}
Libraries & Databases: {json.dumps(tech['libraries'])} / {json.dumps(tech['databases'])}
Folder Tree:
```
{scan['folder_tree'][:3000]}
```

Provide a high-level architecture summary, identify the main components, explain entry points and system data flow, and draw an ASCII component layout.
Be concise, clear, and direct.
"""
    client = _get_gemini_client()
    if not client:
        return _fallback_architecture(scan, tech)
        
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        print(f"[ProjectIntelligence] Gemini analysis failed: {e}")
        return _fallback_architecture(scan, tech)

def _fallback_architecture(scan: dict, tech: dict) -> str:
    # Programmatic text output when offline
    lines = [
        f"--- LOCAL-FIRST ARCHITECTURE REPORT: {scan['project_name']} ---",
        f"Path: {scan['project_path']}",
        f"Languages Detected: {', '.join(tech['languages'])}",
        f"Frameworks Detected: {', '.join(tech['frameworks']) if tech['frameworks'] else 'None'}",
        f"Entry Points: {', '.join(scan['entry_points']) if scan['entry_points'] else 'None'}",
        "\n1. High-Level Directory Overview:",
        scan["folder_tree"][:1500]
    ]
    if scan["import_graph"]:
        lines.append("\n2. Module Import Relationships:")
        for k, v in list(scan["import_graph"].items())[:8]:
            lines.append(f"  - Module '{k}' imports: {', '.join(v)}")
    return "\n".join(lines)

def generate_readme(path: str) -> str:
    scan = scan_project(path)
    if "error" in scan:
        return scan["error"]
        
    tech = detect_tech_stack(path)
    
    prompt = f"""
Generate a professional, high-quality, comprehensive README.md file for the project details below.
Project Name: {scan['project_name']}
Languages: {json.dumps(scan['language_distribution'])}
Frameworks: {json.dumps(tech['frameworks'])}
Databases & Hosting: {json.dumps(tech['databases'])} / {json.dumps(tech['hosting_targets'])}
Folder Tree:
```
{scan['folder_tree'][:2000]}
```

Include:
1. Features
2. Tech Stack
3. Folder Structure
4. Installation instructions (guess based on technology stack)
5. Usage examples
Output ONLY valid markdown content.
"""
    client = _get_gemini_client()
    readme_content = ""
    if client:
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            readme_content = response.text.strip()
            # remove backticks if any
            readme_content = re.sub(r"^```markdown\n|```$", "", readme_content).strip()
        except Exception as e:
            print(f"[ProjectIntelligence] Gemini README generation failed: {e}")
            
    if not readme_content:
        # Fallback readme boilerplate
        readme_content = f"""# {scan['project_name']}

Generated automatically by NEXUS AI Project Intelligence Engine.

## Tech Stack
- **Languages**: {', '.join(tech['languages'])}
- **Frameworks**: {', '.join(tech['frameworks']) if tech['frameworks'] else 'Generic/Static'}
- **Databases**: {', '.join(tech['databases']) if tech['databases'] else 'None/Local'}
- **Hosting Targets**: {', '.join(tech['hosting_targets']) if tech['hosting_targets'] else 'Local'}

## Project Structure
```
{scan['folder_tree'][:1000]}
```

## Setup & Installation
1. Clone the repository.
2. Install dependencies based on setup config files ({', '.join([Path(c).name for c in scan['config_files']])}).
3. Run the application entry points ({', '.join(scan['entry_points'])}).
"""

    # Write to README.md
    try:
        readme_path = Path(path) / "README.md"
        readme_path.write_text(readme_content, encoding="utf-8")
        return f"Successfully generated README.md at {readme_path}"
    except Exception as e:
        return f"Failed to write README.md: {e}\n\nGenerated Content:\n{readme_content[:500]}..."

def generate_project_report(path: str) -> str:
    scan = scan_project(path)
    if "error" in scan:
        return scan["error"]
        
    tech = detect_tech_stack(path)
    smells = find_code_smells(path)
    
    # Calculate Risk Score (programmatic score out of 100)
    high_count = sum(1 for s in smells if s["severity"] == "HIGH")
    med_count = sum(1 for s in smells if s["severity"] == "MEDIUM")
    low_count = sum(1 for s in smells if s["severity"] == "LOW")
    risk_score = min(100, high_count * 25 + med_count * 10 + low_count * 3)
    
    risk_text = "LOW"
    if risk_score > 60: risk_text = "HIGH"
    elif risk_score > 25: risk_text = "MEDIUM"

    report_prompt = f"""
Compile a professional NEXUS AI Project Report.
Project Details:
Name: {scan['project_name']}
File Count: {scan['total_files']}, Dir Count: {scan['total_dirs']}
Lines of Code: {scan['total_lines']}, Size: {scan['total_size_bytes']} bytes
Languages: {json.dumps(scan['language_distribution'])}
Frameworks: {json.dumps(tech['frameworks'])}
Code Smells Detected: {json.dumps(smells[:15])}
Risk Score: {risk_score}/100 ({risk_text})

Write a summary that evaluates:
1. Tech Stack suitability
2. Architecture design patterns
3. Code quality assessment and risks
4. Concrete actionable recommendations for improvements or deployment.

Format as:
NEXUS AI PROJECT REPORT
-----------------------
Project Name: {scan['project_name']}
Technology Stack: [Langs/Frameworks]
Architecture Summary: [Summary]
File Statistics: [Stats]
Code Quality: [Evaluation of code smells and nesting]
Risks: [Primary risks]
Recommendations: [List recommendations]
"""
    client = _get_gemini_client()
    if not client:
        # Fallback local report
        lines = [
            "=========================================",
            "        NEXUS AI PROJECT REPORT",
            "=========================================",
            f"Project Name: {scan['project_name']}",
            f"Project Path: {scan['project_path']}",
            f"Technology Stack: {', '.join(tech['languages'])} | {', '.join(tech['frameworks'])}",
            f"File Statistics: {scan['total_files']} files, {scan['total_dirs']} dirs, {scan['total_lines']} lines of code",
            f"Code Quality Score: {100 - risk_score}/100",
            f"Risks Detected: {high_count} HIGH, {med_count} MEDIUM, {low_count} LOW code smells.",
            "\nRecommendations:",
            "  1. Review hardcoded credentials/tokens if high risk is reported."
        ]
        if high_count > 0:
            lines.append("  2. Refactor complex modules or split large files.")
        lines.append("  3. Set up unit test cases for key components.")
        return "\n".join(lines)
        
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=report_prompt
        )
        return response.text.strip()
    except Exception as e:
        print(f"[ProjectIntelligence] Gemini report failed: {e}")
        return f"Error compiling Gemini report. Fallback stats:\nCode Quality Risk: {risk_text} ({risk_score}/100)"

def answer_project_question(path: str, question: str) -> str:
    scan = scan_project(path)
    if "error" in scan:
        return scan["error"]
        
    tech = detect_tech_stack(path)
    
    # Simple keyword routing: try to read relevant files
    q_lower = question.lower()
    keywords = ["auth", "login", "password", "database", "model", "api", "route", "view", "controller", "settings", "config", "install", "deploy"]
    relevant_files = []
    
    # Match files in the file list
    for entry in scan["entry_points"] + scan["config_files"]:
        relevant_files.append(entry)
        
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
        for file in files:
            for kw in keywords:
                if kw in file.lower():
                    rel_p = Path(root) / file
                    rel_p_str = str(rel_p.relative_to(path))
                    if rel_p_str not in relevant_files:
                        relevant_files.append(rel_p_str)
                        
    # Take first 4 relevant files and read snippets (first 200 lines max) to feed into context
    file_context = []
    for rel_f in relevant_files[:4]:
        full_f = Path(path) / rel_f
        try:
            with open(full_f, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(4000) # first 4000 chars
                file_context.append(f"--- File: {rel_f} ---\n{content}\n")
        except Exception:
            pass
            
    context_str = "\n".join(file_context)
    
    prompt = f"""
You are a software architecture analyst. Answer the user's question about the project based on the following project context.
Project Name: {scan['project_name']}
Languages: {json.dumps(scan['language_distribution'])}
Frameworks: {json.dumps(tech['frameworks'])}
Entry Points: {json.dumps(scan['entry_points'])}
Folder Tree:
```
{scan['folder_tree'][:1500]}
```

Relevant Code Files Content:
{context_str}

Question: "{question}"

Answer the question precisely, pointing out specific filenames or functions if they are found in the context.
"""
    client = _get_gemini_client()
    if not client:
        return f"[OFFLINE MODE] Unable to contact Gemini. Based on file analysis, entry points are {scan['entry_points']} and config files are {scan['config_files']}."
        
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        return f"Gemini Q&A Error: {e}"

# --- Self-Analysis Mode ---

def analyze_self() -> dict:
    scan = scan_project(str(BASE_DIR))
    tech = detect_tech_stack(str(BASE_DIR))
    smells = find_code_smells(str(BASE_DIR))
    
    # Tool Inventory
    from main import TOOL_DECLARATIONS
    tool_inventory = [t["name"] for t in TOOL_DECLARATIONS]
    
    # Capability Report
    capabilities = [
        "Interactive PyQt6 User Interface with HUD canvas visualizer",
        "Asynchronous Gemini Live API websocket interface",
        "Desktop automation (app launcher, mouse/keyboard triggers, browser automation)",
        "System telemetry inspector & telemetry health diagnoses",
        "Vision OCR & screen analysis system",
        "Long-term memory management with semantic key/value database",
        "Project Intelligence Engine for folder/repository scanning and QA"
    ]
    
    # Agent Inventory
    agents = ["planner.py (Gemini-based Task Scheduler)", "executor.py (Sub-task executor, retry fallback agent)"]
    
    # Vision System Inventory
    vision = ["actions/screen_processor.py (pyautogui capturing)", "actions/vision_engine.py (OCR, UI element detection)"]
    
    # Memory System Inventory
    memory = ["memory/memory_manager.py (Memory extract / database save)", "memory/nexus_memory.db (sqlite3 long-term store)"]
    
    # Run Gemini for advanced report consolidator
    report_text = ""
    client = _get_gemini_client()
    if client:
        prompt = f"""
Generate a Self-Analysis Capability & Architecture Report for NEXUS AI.
NEXUS AI Stats:
Files: {scan['total_files']}, Lines: {scan['total_lines']}
Tools: {tool_inventory}
Agents: {agents}
Vision System: {vision}
Memory System: {memory}
Code Smells: {len(smells)} detected.

Please compile:
1. Capability Report (explaining what NEXUS AI is capable of)
2. Architecture Report
3. Tool & Agent Inventory
4. Vision & Memory Inventory
5. Risks & Improvement Opportunities

Write a beautiful, structured report in clean markdown/text format.
"""
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            report_text = response.text.strip()
        except Exception:
            pass
            
    if not report_text:
        # Programmatic self report fallback
        report_text = f"""### Capability Report
NEXUS AI is a personal desktop agent operating system.
Key Capabilities:
{chr(10).join(['- ' + c for c in capabilities])}

### Architecture Report
- Core framework: Python / PyQt6 / Asyncio.
- Agent system: Two-tier agent (Planner/Executor) with dynamic error recovery.
- Vision modules: Screen captures, OCR, and coordinate mappings.
- Memory: Structured key-value store using SQLite3.

### Tool & Agent Inventory
- Agent modules: {', '.join(agents)}
- Tools registered: {', '.join(tool_inventory)}

### Vision & Memory Inventory
- Vision files: {', '.join(vision)}
- Memory database: {', '.join(memory)}

### Risks & Improvement Opportunities
- High dependency on external audio device drivers (`sounddevice` / portaudio).
- Synchronous threading wrappers for legacy blocking actions.
"""
    return {
        "project_name": "NEXUS AI",
        "project_path": str(BASE_DIR),
        "total_files": scan["total_files"],
        "total_lines": scan["total_lines"],
        "language_distribution": scan["language_distribution"],
        "report": report_text
    }

# --- Project Comparison Mode ---

def compare_projects(path_a: str, path_b: str) -> dict:
    scan_a = scan_project(path_a)
    scan_b = scan_project(path_b)
    
    if "error" in scan_a: return scan_a
    if "error" in scan_b: return scan_b
    
    tech_a = detect_tech_stack(path_a)
    tech_b = detect_tech_stack(path_b)
    
    smells_a = find_code_smells(path_a)
    smells_b = find_code_smells(path_b)
    
    # Calculate Complexity Scores (Lines of code * 0.1 + File count * 2 + smells count * 3)
    comp_a = int(scan_a["total_lines"] * 0.1 + scan_a["total_files"] * 2 + len(smells_a) * 3)
    comp_b = int(scan_b["total_lines"] * 0.1 + scan_b["total_files"] * 2 + len(smells_b) * 3)
    
    # Generate Semantic comparison with Gemini
    comparison_report = ""
    client = _get_gemini_client()
    if client:
        prompt = f"""
Compare the following two software projects:
Project A: {scan_a['project_name']}
  - Files: {scan_a['total_files']}, Lines: {scan_a['total_lines']}
  - Tech Stack: {json.dumps(tech_a)}
  - Complexity score: {comp_a}
Project B: {scan_b['project_name']}
  - Files: {scan_b['total_files']}, Lines: {scan_b['total_lines']}
  - Tech Stack: {json.dumps(tech_b)}
  - Complexity score: {comp_b}

Write a side-by-side comparison report covering:
1. Tech Stack Comparison
2. Architecture Comparison
3. Complexity Evaluation
4. Feature Comparison
5. Key Recommendations for both projects.
"""
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            comparison_report = response.text.strip()
        except Exception:
            pass
            
    if not comparison_report:
        # Fallback local comparison report
        comparison_report = f"""### Project Comparison Report
1. **General Comparison**:
   - {scan_a['project_name']}: {scan_a['total_files']} files, {scan_a['total_lines']} LOC. Tech: {', '.join(tech_a['languages'])}
   - {scan_b['project_name']}: {scan_b['total_files']} files, {scan_b['total_lines']} LOC. Tech: {', '.join(tech_b['languages'])}

2. **Complexity evaluation**:
   - Complexity score {scan_a['project_name']}: {comp_a}
   - Complexity score {scan_b['project_name']}: {comp_b}
   (Score based on lines of code, total files, and code smells).

3. **Recommendations**:
   - For {scan_a['project_name']}: Audit folder structures.
   - For {scan_b['project_name']}: Review dependencies periodically.
"""

    return {
        "project_a": scan_a["project_name"],
        "project_b": scan_b["project_name"],
        "complexity_a": comp_a,
        "complexity_b": comp_b,
        "languages_a": tech_a["languages"],
        "languages_b": tech_b["languages"],
        "report": comparison_report
    }
