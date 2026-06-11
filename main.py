import asyncio
import threading
import json
import sys
import traceback
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

import sounddevice as sd
from google import genai
from google.genai import types
from ui import NexusAIUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    should_extract_memory, extract_memory
)
from memory.memory_engine import MemoryEngine

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.system_info       import (
    get_system_info, is_system_info_query,
    is_performance_monitor_query, is_system_health_query,
    is_app_detection_query, is_hardware_recommendation_query,
    is_system_diagnostics_query, is_storage_analyzer_query
)


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "NEXUS AI is an advanced personal AI operating system developed by Ayushh. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )
    
_last_memory_input = ""

def _update_memory_async(user_text: str, nexus_text: str, ui=None) -> None:
    global _last_memory_input

    user_text   = (user_text   or "").strip()
    nexus_text  = (nexus_text  or "").strip()

    if len(user_text) < 5 or user_text == _last_memory_input:
        return
    _last_memory_input = user_text

    # Take workspace snapshot & run background consolidation, aging, capability checks
    try:
        engine = MemoryEngine()
        import subprocess
        branch = "main"
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(BASE_DIR), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2
            )
            if res.returncode == 0:
                branch = res.stdout.strip()
        except Exception:
            pass
            
        open_files = [ui.current_file] if (ui and ui.current_file) else []
        engine.save_workspace_snapshot(
            active_project=str(BASE_DIR),
            open_files=open_files,
            active_branch=branch,
            current_task=""
        )
        
        # Background consolidation & aging
        engine.consolidate_memories()
        engine.run_aging_lifecycle()
        engine.trigger_auto_analysis()
    except Exception as e:
        print(f"[MemoryEngine] Background tasks failed: {e}")

    try:
        api_key = _get_api_key()
        if not should_extract_memory(user_text, nexus_text, api_key):
            return
        data = extract_memory(user_text, nexus_text, api_key)
        if data:
            update_memory(data)
            print(f"[Memory] ✅ {list(data.keys())}")
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ {e}")

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the Windows computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Windows Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls the web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, any web-based task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | press | close"},
                "url":         {"type": "STRING", "description": "URL for go_to action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up or down for scroll"},
                "key":         {"type": "STRING", "description": "Key name for press action"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage, edit, validate, generate_project.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info | edit | validate | generate_project"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write/edit"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
                "edit_type":   {"type": "STRING", "description": "append | replace | insert"},
                "target":      {"type": "STRING", "description": "Target string to replace or insert relative to"},
                "position":    {"type": "STRING", "description": "before | after | start | end"},
                "validation_type": {"type": "STRING", "description": "exists | size | permissions"},
                "project_description": {"type": "STRING", "description": "Prompt/instructions for generating custom project structure"},
                "project_name": {"type": "STRING", "description": "Folder name of the project to generate"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
    "name": "shutdown_nexus",
    "description": (
        "Shuts down the assistant completely. "
        "Call this when the user expresses intent to end the conversation, "
        "close the assistant, say goodbye, or stop Nexus AI. "
        "The user can say this in ANY language."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {},
    }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Ayushh, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "get_system_info",
        "description": "Inspects the local system hardware, software, battery, and network configurations to return real-time system specifications.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "component": {
                    "type": "STRING",
                    "description": "Optional component to query: 'cpu' | 'ram' | 'gpu' | 'storage' | 'os' | 'battery' | 'network' | 'device' | 'all' (default: 'all')"
                }
            },
            "required": []
        }
    },
    {
        "name": "get_performance_metrics",
        "description": "Inspects real-time telemetry including CPU usage, RAM usage, GPU usage, GPU temperature, CPU temperature, battery percentage, and network status.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "check_system_health",
        "description": "Performs a local system health check returning overall score, CPU/RAM/Battery/Storage statuses, and recommendations.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_running_apps",
        "description": "Lists the running user-facing applications and process groups, sorted by memory footprint, showing CPU %, RAM, and Window Titles.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_hardware_recommendations",
        "description": "Analyzes RAM speed, motherboard slots, CPU gen, GPU model, and SSDs to return upgrade suggestions and AI/ML local execution suitability.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Optional specific hardware question"}
            },
            "required": []
        }
    },
    {
        "name": "diagnose_system",
        "description": "Performs diagnostics on high resource usage, disk headroom, excessive startup apps, thermals, and battery wear, returning causes and actions ranked by confidence.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "analyze_storage",
        "description": "Walks major user directories (Downloads, Documents, Desktop, Videos, Pictures) to report folder sizes, total disk percentages, and the largest files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "scan_project",
        "description": "Scans folder structure, configurations, and file lists of a project recursively.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Project root folder path"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "analyze_architecture",
        "description": "Generates high-level architecture component layout and entry points of a codebase.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Project root folder path"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "detect_tech_stack",
        "description": "Identifies programming languages, frameworks, libraries, and database tools used in a codebase.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Project root folder path"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "find_code_smells",
        "description": "Analyzes a project's files for code smells, large structures, nesting, and risks.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Project root folder path"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "generate_project_report",
        "description": "Creates a structured NEXUS AI Project Report detailing stack, stats, risks, and suggestions.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Project root folder path"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "generate_readme",
        "description": "Automatically generates a professional README.md file inside the project directory.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Project root folder path"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "answer_project_question",
        "description": "Answers a codebase-specific developer question using local context.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Project root folder path"},
                "question": {"type": "STRING", "description": "The developer question to answer"}
            },
            "required": ["path", "question"]
        }
    },
]


class NexusLive:

    def __init__(self, ui: NexusAIUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self.speaking_start_time = None
        self.ui.on_text_command = self._on_text_command

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return

        # Log user text query
        try:
            MemoryEngine().log_chat_turn("user", text)
        except Exception as e:
            print(f"[MemoryEngine] Chat turn logging failed: {e}")

        t_lower = text.lower().strip()
        vision_keywords = [
            "what is on my screen", "what is on screen", "analyze my screen", "analyze the screen",
            "describe current window", "describe my active window", "explain this error",
            "explain the error", "read screen text", "read text on screen", "read this page",
            "detect ui elements", "detect elements on screen"
        ]
        
        is_vision = any(kw in t_lower for kw in vision_keywords)
        if is_vision:
            def run_vision_worker():
                try:
                    self.ui.set_state("PROCESSING")
                    self.ui.show_vision_panel()
                    
                    from actions.vision_engine import vision_assistant, analyze_screen
                    result = vision_assistant(text)
                    
                    self.ui.write_log(f"NEXUS: {result}")
                    try:
                        self.ui.update_vision_panel(analyze_screen())
                    except Exception as ve_err:
                        print(f"[VisionEngine] Panel update failed: {ve_err}")
                        import traceback
                        traceback.print_exc()
                    self.speak(result)
                except Exception as e:
                    print(f"[VisionEngine] Worker thread failed: {e}")
                    import traceback
                    traceback.print_exc()
                    self.ui.write_log(f"SYS: Vision analysis failed: {e}")
                    self.speak(f"Vision analysis encountered an error: {str(e)[:100]}")
                finally:
                    if not self.ui.muted:
                        self.ui.set_state("LISTENING")
                
            threading.Thread(target=run_vision_worker, daemon=True).start()
            return

        # Project Intelligence Intercept cases
        proj_keywords = [
            "analyze this project", "analyze repository", "analyze this repository", "generate readme", "generate a readme",
            "explain this codebase", "explain the codebase", "find bugs in this project", "find bugs in the project",
            "what technologies are used?", "what tech stack is used", "how is this project structured?", "how is the project structured?",
            "analyze yourself", "compare nexus"
        ]
        is_proj = any(kw in t_lower for kw in proj_keywords)
        if is_proj:
            def run_project_worker():
                try:
                    self.ui.set_state("PROCESSING")
                    
                    # Resolve path
                    path = None
                    if "careernova" in t_lower:
                        path = r"c:\Users\ayush\OneDrive\Desktop\Private\Careernova"
                    elif "creditwise" in t_lower:
                        path = r"C:\Users\ayush\Downloads\Telegram Desktop\DAY 27 CreditWise Loan System(Minor-Project)\DAY 27 CreditWise Loan System(Minor-Project)\Day 27 CreditWise Loan System(Minor-Project) TEAMWORK"
                    elif "nexus" in t_lower:
                        path = str(BASE_DIR)
                    else:
                        if self.ui.current_file:
                            p = Path(self.ui.current_file)
                            path = str(p.parent if p.is_file() else p)
                        else:
                            path = str(BASE_DIR)

                    from actions.project_intelligence import (
                        scan_project, detect_tech_stack, find_code_smells,
                        analyze_architecture, generate_project_report,
                        generate_readme, answer_project_question,
                        analyze_self, compare_projects
                    )
                    
                    # Check Self-Analysis
                    if "analyze yourself" in t_lower:
                        self.ui.write_log("SYS: Running Self-Analysis Mode...")
                        res = analyze_self()
                        self.ui.show_project_intelligence()
                        self.ui.update_project_intelligence(res)
                        msg = "Self-analysis complete. capability and architecture reports loaded."
                        self.ui.write_log(f"NEXUS: {msg}")
                        self.speak(msg)
                        return
                        
                    # Check Comparative
                    if "compare" in t_lower and ("careernova" in t_lower or "nexus" in t_lower):
                        self.ui.write_log("SYS: Running Project Comparison Mode...")
                        path_a = str(BASE_DIR)
                        path_b = r"c:\Users\ayush\OneDrive\Desktop\Private\Careernova"
                        res = compare_projects(path_a, path_b)
                        self.ui.show_project_intelligence()
                        self.ui.update_project_intelligence({
                            "project_name": "Project Comparison",
                            "project_path": f"{res['project_a']} vs {res['project_b']}",
                            "total_files": 0,
                            "total_dirs": 0,
                            "language_distribution": {},
                            "report": res["report"]
                        })
                        msg = f"Comparison between {res['project_a']} and {res['project_b']} is loaded in the intelligence window."
                        self.ui.write_log(f"NEXUS: {msg}")
                        self.speak(msg)
                        return

                    # Default Project Analysis Actions
                    scan = scan_project(path)
                    if "error" in scan:
                        self.ui.write_log(f"SYS: {scan['error']}")
                        self.speak(scan["error"])
                        return
                        
                    self.ui.show_project_intelligence()
                    self.ui.update_project_intelligence(scan)
                    
                    if "generate readme" in t_lower:
                        self.ui.write_log("SYS: Generating README...")
                        res = generate_readme(path)
                        self.ui.write_log(f"NEXUS: {res}")
                        self.speak("README generated successfully.")
                    elif "find bugs" in t_lower or "code smells" in t_lower:
                        self.ui.write_log("SYS: Scanning for code smells...")
                        smells = find_code_smells(path)
                        report = generate_project_report(path)
                        scan["report"] = report
                        self.ui.update_project_intelligence(scan)
                        msg = f"Code smell scan found {len(smells)} potential improvements. Details are displayed."
                        self.ui.write_log(f"NEXUS: {msg}")
                        self.speak(msg)
                    elif "tech stack" in t_lower or "technologies" in t_lower:
                        self.ui.write_log("SYS: Detecting technology stack...")
                        tech = detect_tech_stack(path)
                        scan["report"] = f"Languages: {', '.join(tech['languages'])}\nFrameworks: {', '.join(tech['frameworks'])}\nDatabases: {', '.join(tech['databases'])}"
                        self.ui.update_project_intelligence(scan)
                        msg = f"Detected stack: {', '.join(tech['frameworks'] or tech['languages'])}."
                        self.ui.write_log(f"NEXUS: {msg}")
                        self.speak(msg)
                    elif "structure" in t_lower or "architecture" in t_lower or "explain" in t_lower:
                        self.ui.write_log("SYS: Analyzing codebase architecture...")
                        arch = analyze_architecture(path)
                        scan["report"] = arch
                        self.ui.update_project_intelligence(scan)
                        msg = "Codebase architecture analyzed. component map is displayed."
                        self.ui.write_log(f"NEXUS: {msg}")
                        self.speak(msg)
                    else:
                        self.ui.write_log("SYS: Running codebase audit...")
                        report = generate_project_report(path)
                        scan["report"] = report
                        self.ui.update_project_intelligence(scan)
                        msg = f"Analysis of project {scan['project_name']} is complete."
                        self.ui.write_log(f"NEXUS: {msg}")
                        self.speak(msg)
                        
                except Exception as e:
                    print(f"[ProjectIntelligence] Worker thread failed: {e}")
                    import traceback
                    traceback.print_exc()
                    self.ui.write_log(f"SYS: Project intelligence run failed: {e}")
                    self.speak("I encountered an error during project analysis.")
                finally:
                    if not self.ui.muted:
                        self.ui.set_state("LISTENING")

            threading.Thread(target=run_project_worker, daemon=True).start()
            return

        if is_system_info_query(text):
            result = get_system_info(parameters={}, player=self.ui)
            self.ui.write_log(result)
            self.speak("Here is your local system report.")
            return

        if is_performance_monitor_query(text):
            t = text.lower()
            if "close" in t or "stop" in t:
                self.ui.close_performance_monitor()
                self.ui.write_log("SYS: Closing performance monitor.")
                self.speak("Closing performance monitor.")
            else:
                self.ui.show_performance_monitor()
                from actions.system_info import get_performance_metrics
                result = get_performance_metrics(parameters={}, player=self.ui)
                self.ui.write_log(result)
                self.speak("Opening performance monitor and showing live metrics.")
            return

        if is_system_health_query(text):
            from actions.system_info import check_system_health
            result = check_system_health(parameters={}, player=self.ui)
            self.ui.write_log(result)
            self.speak("Running a system health check. Here is the report.")
            return

        if is_app_detection_query(text):
            from actions.system_info import get_running_apps
            result = get_running_apps(parameters={}, player=self.ui)
            self.ui.write_log(result)
            self.speak("Gathering active programs. Here is the list.")
            return

        if is_hardware_recommendation_query(text):
            from actions.system_info import get_hardware_recommendations
            result = get_hardware_recommendations(parameters={"query": text}, player=self.ui)
            self.ui.write_log(result)
            self.speak("Analyzing hardware configuration and AI suitability.")
            return

        if is_system_diagnostics_query(text):
            from actions.system_info import diagnose_system
            result = diagnose_system(parameters={}, player=self.ui)
            self.ui.write_log(result)
            self.speak("Running diagnostics. Here is the performance analysis.")
            return

        if is_storage_analyzer_query(text):
            from actions.system_info import analyze_storage
            result = analyze_storage(parameters={}, player=self.ui)
            self.ui.write_log(result)
            self.speak("Scanning user folders for storage utilization.")
            return

        # Check recall commands
        recall_keywords = [
            "working on yesterday", "yesterday's session",
            "continue my last coding session", "continue last session", "continue last coding session",
            "architecture decisions", "show decisions",
            "vision mode history", "show vision history",
            "bugs were fixed", "fixed bugs",
            "project milestones", "show milestones"
        ]
        is_recall = any(kw in t_lower for kw in recall_keywords) or t_lower.startswith("recall:") or t_lower.startswith("search memory")
        if is_recall:
            def run_recall_worker():
                try:
                    self.ui.set_state("PROCESSING")
                    engine = MemoryEngine()
                    result = engine.handle_recall_command(text)
                    self.ui.write_log(f"NEXUS Memory: {result}")
                    first_line = result.split("\n")[0]
                    self.speak(first_line)
                except Exception as e:
                    print(f"[MemoryEngine] Recall worker failed: {e}")
                    self.ui.write_log(f"SYS: Memory recall failed: {e}")
                    self.speak("I encountered an error recalling that from memory.")
                finally:
                    if not self.ui.muted:
                        self.ui.set_state("LISTENING")
            threading.Thread(target=run_recall_worker, daemon=True).start()
            return

        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def set_speaking(self, value: bool):
        from datetime import datetime
        import time
        with self._speaking_lock:
            prev = self._is_speaking
            if prev != value:
                self._is_speaking = value
                
                prev_state = "SPEAKING" if prev else "LISTENING"
                curr_state = "SPEAKING" if value else "LISTENING"
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                
                print(
                    f"\n[VOICE]\n"
                    f"Previous State: {prev_state}\n"
                    f"Current State: {curr_state}\n"
                    f"Timestamp: {now_str}\n"
                )
                
                if value:
                    self.speaking_start_time = time.time()
                    print("\n[VOICE]\nTTS Started: True\n")
                    self.ui.set_state("SPEAKING")
                else:
                    self.speaking_start_time = None
                    print("\n[VOICE]\nTTS Finished: True\n")
                    print("\n[AUDIO]\nReturned to LISTENING\n")
                    if not self.ui.muted:
                        self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"{tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[NEXUS AI] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")
        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."


            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(goal=args.get("goal", ""), priority=priority, speak=self.speak)
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "get_system_info":
                r = await loop.run_in_executor(None, lambda: get_system_info(parameters=args, player=self.ui))
                result = r or "System specifications gathered."

            elif name == "get_performance_metrics":
                self.ui.show_performance_monitor()
                from actions.system_info import get_performance_metrics
                r = await loop.run_in_executor(None, lambda: get_performance_metrics(parameters=args, player=self.ui))
                result = r or "Performance metrics gathered."

            elif name == "check_system_health":
                from actions.system_info import check_system_health
                r = await loop.run_in_executor(None, lambda: check_system_health(parameters=args, player=self.ui))
                result = r or "Health report generated."

            elif name == "get_running_apps":
                from actions.system_info import get_running_apps
                r = await loop.run_in_executor(None, lambda: get_running_apps(parameters=args, player=self.ui))
                result = r or "Running apps gathered."

            elif name == "get_hardware_recommendations":
                from actions.system_info import get_hardware_recommendations
                r = await loop.run_in_executor(None, lambda: get_hardware_recommendations(parameters=args, player=self.ui))
                result = r or "Hardware recommendations generated."

            elif name == "diagnose_system":
                from actions.system_info import diagnose_system
                r = await loop.run_in_executor(None, lambda: diagnose_system(parameters=args, player=self.ui))
                result = r or "System diagnostics completed."

            elif name == "analyze_storage":
                from actions.system_info import analyze_storage
                r = await loop.run_in_executor(None, lambda: analyze_storage(parameters=args, player=self.ui))
                result = r or "Storage analysis completed."

            elif name == "scan_project":
                from actions.project_intelligence import scan_project
                path = args.get("path", ".")
                r = await loop.run_in_executor(None, lambda: scan_project(path))
                if not r.get("error"):
                    self.ui.show_project_intelligence()
                    self.ui.update_project_intelligence(r)
                    result = f"Scanned project: {r.get('project_name')}"
                else:
                    result = r.get("error")

            elif name == "analyze_architecture":
                from actions.project_intelligence import analyze_architecture, scan_project
                path = args.get("path", ".")
                r_scan = await loop.run_in_executor(None, lambda: scan_project(path))
                r = await loop.run_in_executor(None, lambda: analyze_architecture(path))
                if not r_scan.get("error"):
                    r_scan["report"] = r
                    self.ui.show_project_intelligence()
                    self.ui.update_project_intelligence(r_scan)
                result = r

            elif name == "detect_tech_stack":
                from actions.project_intelligence import detect_tech_stack
                path = args.get("path", ".")
                r = await loop.run_in_executor(None, lambda: detect_tech_stack(path))
                result = f"Tech stack detected: {r}"

            elif name == "find_code_smells":
                from actions.project_intelligence import find_code_smells, scan_project, generate_project_report
                path = args.get("path", ".")
                r_scan = await loop.run_in_executor(None, lambda: scan_project(path))
                r_smells = await loop.run_in_executor(None, lambda: find_code_smells(path))
                r_report = await loop.run_in_executor(None, lambda: generate_project_report(path))
                if not r_scan.get("error"):
                    r_scan["report"] = r_report
                    self.ui.show_project_intelligence()
                    self.ui.update_project_intelligence(r_scan)
                result = f"Found {len(r_smells)} code smells."

            elif name == "generate_project_report":
                from actions.project_intelligence import generate_project_report, scan_project
                path = args.get("path", ".")
                r_scan = await loop.run_in_executor(None, lambda: scan_project(path))
                r = await loop.run_in_executor(None, lambda: generate_project_report(path))
                if not r_scan.get("error"):
                    r_scan["report"] = r
                    self.ui.show_project_intelligence()
                    self.ui.update_project_intelligence(r_scan)
                result = r

            elif name == "generate_readme":
                from actions.project_intelligence import generate_readme
                path = args.get("path", ".")
                r = await loop.run_in_executor(None, lambda: generate_readme(path))
                result = r

            elif name == "answer_project_question":
                from actions.project_intelligence import answer_project_question
                path = args.get("path", ".")
                question = args.get("question", "")
                r = await loop.run_in_executor(None, lambda: answer_project_question(path, question))
                result = r

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "shutdown_nexus":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("NEXUS AI offline.")
                
                try:
                    MemoryEngine().end_session()
                except Exception as e:
                    print(f"[MemoryEngine] Failed to end session on shutdown: {e}")

                def _shutdown():
                    import time, sys, os
                    time.sleep(1)
                    os._exit(0)

                threading.Thread(target=_shutdown, daemon=True).start()
            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[NEXUS AI] 📤 {name} → {str(result)[:80]}")

        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[NEXUS AI] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                nexus_speaking = self._is_speaking
            if not nexus_speaking and not self.ui.muted:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[NEXUS AI] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[NEXUS AI] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[NEXUS AI] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        print(f"\n[AUDIO]\nChunk received: {len(response.data)}\n")
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            self.set_speaking(True)
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                if not in_buf:
                                    print("\n[VOICE]\nSTT Started: True\n")
                                in_buf.append(txt)

                        if sc.turn_complete:
                            print("\n[AUDIO]\nTurn complete received\n")
                            self.audio_in_queue.put_nowait(None)
                            print("\n[AUDIO]\nSentinel queued\n")

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                print("\n[VOICE]\nSTT Finished: True\n")
                                try:
                                    MemoryEngine().log_chat_turn("user", full_in)
                                except Exception as e:
                                    print(f"[MemoryEngine] Voice input log error: {e}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"NEXUS AI: {full_out}")
                                try:
                                    MemoryEngine().log_chat_turn("model", full_out)
                                except Exception as e:
                                    print(f"[MemoryEngine] Voice output log error: {e}")
                            out_buf = []

                            if full_in and len(full_in) > 5:
                                threading.Thread(
                                    target=_update_memory_async,
                                    args=(full_in, full_out, self.ui),
                                    daemon=True
                                ).start()

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[NEXUS AI] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )

        except Exception as e:
            print(f"[NEXUS AI] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[NEXUS AI] 🔊 Play started")
        loop = asyncio.get_event_loop()

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()
        is_playing = False
        try:
            while True:
                chunk = await self.audio_in_queue.get()
                if chunk is None:
                    self.set_speaking(False)
                    if is_playing:
                        print("\n[AUDIO]\nPlayback finished\n")
                        is_playing = False
                    continue

                if not is_playing:
                    print("\n[AUDIO]\nPlayback started\n")
                    is_playing = True

                self.set_speaking(True)
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[NEXUS AI] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            if is_playing:
                print("\n[AUDIO]\nPlayback finished\n")
            stream.stop()
            stream.close()

    async def _failsafe_monitor(self):
        import time
        while True:
            await asyncio.sleep(0.5)
            if self._is_speaking and self.speaking_start_time:
                if time.time() - self.speaking_start_time > 10.0:
                    print("\n[VOICE] ⚠️ SPEAKING state exceeded 10 seconds failsafe. Resetting pipeline.\n")
                    if self.audio_in_queue:
                        while not self.audio_in_queue.empty():
                            try:
                                self.audio_in_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                    self.set_speaking(False)

    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        while True:
            try:
                print("[NEXUS AI] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=10)

                    print("[NEXUS AI] ✅ Connected.")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: NEXUS AI online.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._failsafe_monitor())
                    
            except Exception as e:
                print(f"[NEXUS AI] ⚠️ {e}")
                traceback.print_exc()

            self.set_speaking(False)
            self.ui.set_state("THINKING")
            print("[NEXUS AI] 🔄 Reconnecting in 3s...")
            await asyncio.sleep(3)

def start_global_hotkey_thread(ui):
    def hotkey_thread():
        import ctypes
        import ctypes.wintypes
        
        user32 = ctypes.windll.user32
        byref = ctypes.byref
        
        HOTKEY_ID = 100
        MOD_CONTROL_SHIFT = 0x0002 | 0x0004
        VK_N = 0x4E
        
        # Unregister just in case
        user32.UnregisterHotKey(None, HOTKEY_ID)
        
        if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL_SHIFT, VK_N):
            print("[VisionEngine] Global hotkey Ctrl+Shift+N registration failed.")
            return
            
        print("[VisionEngine] Global hotkey Ctrl+Shift+N registered successfully.")
        try:
            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(byref(msg), None, 0, 0) != 0:
                if msg.message == 0x0312:  # WM_HOTKEY
                    if msg.wParam == HOTKEY_ID:
                        print("[VisionEngine] Hotkey Ctrl+Shift+N pressed!")
                        if ui.on_text_command:
                            ui.on_text_command("Analyze my screen")
                user32.TranslateMessage(byref(msg))
                user32.DispatchMessageW(byref(msg))
        except Exception as e:
            print(f"[VisionEngine] Global hotkey error: {e}")
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            
    threading.Thread(target=hotkey_thread, daemon=True).start()


def main():
    import os
    import shutil
    import sys
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

    # Migration checks
    jarvis_config_path = BASE_DIR / "config" / "jarvis_config.json"
    nexus_config_path = BASE_DIR / "config" / "nexus_config.json"
    if jarvis_config_path.exists() and not nexus_config_path.exists():
        try:
            shutil.copy2(jarvis_config_path, nexus_config_path)
            print("[Migration] Migrated jarvis_config.json to nexus_config.json")
        except Exception as e:
            print(f"[Migration] Warning: Failed to migrate jarvis_config.json: {e}")

    jarvis_memory_db = BASE_DIR / "memory" / "jarvis_memory.db"
    nexus_memory_db = BASE_DIR / "memory" / "nexus_memory.db"
    if jarvis_memory_db.exists() and not nexus_memory_db.exists():
        try:
            shutil.copy2(jarvis_memory_db, nexus_memory_db)
            print("[Migration] Migrated jarvis_memory.db to nexus_memory.db")
        except Exception as e:
            print(f"[Migration] Warning: Failed to migrate jarvis_memory.db: {e}")

    # Professional startup banner printout
    print("==================================================")
    print("                 NEXUS AI ⭐")
    print("        Personal AI Operating System")
    print("==================================================")
    print("\nOwner: Ayushh\n")
    print("NEXUS AI initialized.")
    print("Memory systems active.")
    print("Voice interface online.")
    print("Awaiting instructions.\n")

    ui = NexusAIUI("face.png")
    start_global_hotkey_thread(ui)

    def runner():
        ui.wait_for_api_key()
        try:
            MemoryEngine().start_session(active_workspace=str(BASE_DIR))
        except Exception as e:
            print(f"[MemoryEngine] Failed to start startup session: {e}")
        nexus = NexusLive(ui)
        try:
            asyncio.run(nexus.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


# Compatibility aliases
JarvisUI = NexusAIUI
JarvisLive = NexusLive

if __name__ == "__main__":
    main()