# actions/project_generator.py
import json
import re
import sys
from pathlib import Path
from datetime import datetime
import google.generativeai as genai
from actions.workspace_registry import register_project, register_file_creation, log_action, register_files_creation

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

def _get_gemini():
    genai.configure(api_key=_get_api_key())
    return genai.GenerativeModel("gemini-2.5-flash")

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()

def _resolve_project_dir(project_name: str, parent_dir: str = "desktop") -> Path:
    from core.file_manager import get_desktop_path
    desktop = get_desktop_path()
    shortcuts = {
        "desktop": desktop,
        "downloads": Path.home() / "Downloads",
        "documents": Path.home() / "Documents",
        "home": Path.home()
    }
    
    parent_path = shortcuts.get(parent_dir.lower().strip(), Path(parent_dir).expanduser())
    if not parent_path.is_absolute():
        parent_path = desktop
        
    return parent_path / project_name

def generate_project(description: str, project_name: str = "", parent_dir: str = "desktop") -> dict:
    """
    Generates a complete project structure from instructions.
    Uses fast templates for common frameworks or falls back to Gemini for custom setups.
    Returns a status report dictionary.
    """
    desc_lower = description.lower()
    
    # 1. Determine Project Name
    if not project_name:
        if "flask" in desc_lower:
            project_name = "flask_app"
        elif "express" in desc_lower or "node" in desc_lower:
            project_name = "express_api"
        elif "react" in desc_lower:
            project_name = "react_app"
        else:
            project_name = "nexus_project"
            
    # Clean project name
    project_name = re.sub(r"[^\w\-]", "_", project_name)
    project_dir = _resolve_project_dir(project_name, parent_dir)
    
    # Boundary Enforcement
    from actions.file_controller import validate_path_safety
    is_safe, err = validate_path_safety(project_dir)
    if not is_safe:
        log_action("generate_project", str(project_dir), "failure", f"Safety Violation: {err}")
        return {
            "status": "failure",
            "project_name": project_name,
            "project_path": str(project_dir),
            "files_created": [],
            "message": f"Safety Violation: {err}"
        }
        
    print(f"[ProjectGenerator] Creating project '{project_name}' at: {project_dir}")
    
    files_created = []
    
    # 2. Identify and execute standard templates
    if "flask" in desc_lower:
        files_created = _generate_flask_template(project_dir)
    elif "express" in desc_lower or "node" in desc_lower:
        files_created = _generate_node_template(project_dir)
    elif "react" in desc_lower:
        files_created = _generate_react_template(project_dir)
    else:
        # Fallback to Gemini for custom project generation
        files_created = _generate_custom_via_gemini(description, project_name, project_dir)
        
    # 3. Register in workspace registry
    if files_created:
        register_project(project_name, str(project_dir), description)
        batch_files = [(str(project_dir / f[0]), _detect_asset_type(project_dir / f[0])) for f in files_created]
        register_files_creation(batch_files, project_name)
            
        report = {
            "status": "success",
            "project_name": project_name,
            "project_path": str(project_dir),
            "files_created": [str(project_dir / f[0]) for f in files_created],
            "message": f"Successfully generated project '{project_name}' with {len(files_created)} files."
        }
    else:
        report = {
            "status": "failure",
            "project_name": project_name,
            "project_path": str(project_dir),
            "files_created": [],
            "message": "Failed to generate project files."
        }
        
    return report

def _detect_asset_type(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext in {"py", "js", "ts", "html", "css"}: return "code"
    if ext in {"txt", "md"}: return "document"
    if ext in {"json", "csv"}: return "data"
    return "other"

def _generate_flask_template(project_dir: Path) -> list:
    project_dir.mkdir(parents=True, exist_ok=True)
    
    files = [
        ("app.py", 'from flask import Flask, render_template\n\napp = Flask(__name__)\n\n@app.route("/")\ndef index():\n    return render_template("index.html")\n\nif __name__ == "__main__":\n    app.run(debug=True)\n'),
        ("requirements.txt", "Flask==3.0.2\n"),
        ("templates/index.html", '<!DOCTYPE html>\n<html lang="en">\n<head>\n    <meta charset="UTF-8">\n    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n    <title>NEXUS Flask App</title>\n    <link rel="stylesheet" href="/static/style.css">\n</head>\n<body>\n    <div class="container">\n        <h1>Welcome to your NEXUS Flask Project!</h1>\n        <p>This project was dynamically generated by NEXUS AI.</p>\n    </div>\n</body>\n</html>\n'),
        ("static/style.css", 'body {\n    font-family: Arial, sans-serif;\n    background-color: #f4f4f9;\n    color: #333;\n    margin: 0;\n    padding: 0;\n    display: flex;\n    justify-content: center;\n    align-items: center;\n    height: 100vh;\n}\n.container {\n    text-align: center;\n    padding: 2rem;\n    background: white;\n    border-radius: 8px;\n    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);\n}\n'),
        ("README.md", "# Flask App\n\nGenerated by NEXUS AI.\n\n## Setup\n\n1. Install dependencies:\n   ```bash\n   pip install -r requirements.txt\n   ```\n2. Run the application:\n   ```bash\n   python app.py\n   ```\n")
    ]
    
    for rel_path, content in files:
        full_path = project_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        
    return files

def _generate_node_template(project_dir: Path) -> list:
    project_dir.mkdir(parents=True, exist_ok=True)
    
    package_json = {
        "name": project_dir.name,
        "version": "1.0.0",
        "description": "Node Express App generated by NEXUS AI",
        "main": "index.js",
        "scripts": {
            "start": "node index.js"
        },
        "dependencies": {
            "express": "^4.19.2"
        }
    }
    
    files = [
        ("index.js", 'const express = require("express");\nconst app = express();\nconst PORT = process.env.PORT || 3000;\n\napp.use(express.json());\n\napp.get("/", (req, res) => {\n    res.json({ message: "Welcome to Node/Express App generated by NEXUS AI" });\n});\n\napp.listen(PORT, () => {\n    console.log(`Server is running on port ${PORT}`);\n});\n'),
        ("package.json", json.dumps(package_json, indent=2)),
        ("README.md", "# Node Express App\n\nGenerated by NEXUS AI.\n\n## Setup\n\n1. Install dependencies:\n   ```bash\n   npm install\n   ```\n2. Start server:\n   ```bash\n   npm start\n   ```\n")
    ]
    
    for rel_path, content in files:
        full_path = project_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        
    return files

def _generate_react_template(project_dir: Path) -> list:
    project_dir.mkdir(parents=True, exist_ok=True)
    
    package_json = {
        "name": project_dir.name,
        "version": "1.0.0",
        "private": True,
        "dependencies": {
            "react": "^18.3.1",
            "react-dom": "^18.3.1",
            "react-scripts": "5.0.1"
        },
        "scripts": {
            "start": "react-scripts start",
            "build": "react-scripts build"
        }
    }
    
    files = [
        ("package.json", json.dumps(package_json, indent=2)),
        ("public/index.html", '<!DOCTYPE html>\n<html lang="en">\n<head>\n    <meta charset="utf-8" />\n    <title>NEXUS React App</title>\n</head>\n<body>\n    <div id="root"></div>\n</body>\n</html>\n'),
        ("src/index.js", 'import React from "react";\nimport ReactDOM from "react-dom/client";\nimport App from "./App";\nimport "./index.css";\n\nconst root = ReactDOM.createRoot(document.getElementById("root"));\nroot.render(\n  <React.StrictMode>\n    <App />\n  </React.StrictMode>\n);\n'),
        ("src/App.js", 'import React from "react";\n\nfunction App() {\n  return (\n    <div style={{ textAlign: "center", marginTop: "50px" }}>\n      <h1>Hello from NEXUS React App!</h1>\n      <p>This is standard template generated by NEXUS AI.</p>\n    </div>\n  );\n}\n\nexport default App;\n'),
        ("src/index.css", 'body {\n  margin: 0;\n  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;\n  background-color: #282c34;\n  color: white;\n}\n'),
        ("README.md", "# React App\n\nGenerated by NEXUS AI.\n\n## Setup\n\n1. Install dependencies:\n   ```bash\n   npm install\n   ```\n2. Run development server:\n   ```bash\n   npm start\n   ```\n")
    ]
    
    for rel_path, content in files:
        full_path = project_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        
    return files

def _generate_custom_via_gemini(description: str, project_name: str, project_dir: Path) -> list:
    model = _get_gemini()
    
    # Step A: Ask Gemini for the list of files needed
    plan_prompt = f"""You are a senior project architect. Create a plan for the project: '{project_name}'.
Description: {description}

Return ONLY valid JSON mapping relative file paths to description/purpose. 
No markdown, no explainers, no backticks.
Format:
{{
  "files": [
    {{
      "path": "relative/path/to/file.ext",
      "description": "What this file does"
    }}
  ]
}}
"""
    
    try:
        response = model.generate_content(plan_prompt)
        plan_raw = _strip_fences(response.text)
        plan = json.loads(plan_raw)
        files_plan = plan.get("files", [])
    except Exception as e:
        print(f"[ProjectGenerator] Gemini plan generation failed: {e}")
        # Default simple python layout
        files_plan = [
            {"path": "main.py", "description": "Entrypoint script"},
            {"path": "README.md", "description": "Project documentation"}
        ]
        
    files_created = []
    
    # Step B: For each file in the plan, ask Gemini to generate code
    for file_info in files_plan:
        path_str = file_info["path"]
        file_desc = file_info["description"]
        
        file_prompt = f"""You are a professional software engineer. 
Write the complete, production-ready code content for the file '{path_str}' in the project '{project_name}'.
Goal: {description}
File purpose: {file_desc}

Provide ONLY the code contents. No triple backticks, no markdown, no comments explaining the code unless inline.
"""
        try:
            response = model.generate_content(file_prompt)
            content = _strip_fences(response.text)
        except Exception as e:
            content = f"# Failed to generate: {e}"
            
        full_path = project_dir / path_str
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        files_created.append((path_str, content))
        
    return files_created
