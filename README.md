# 🤖 NEXUS AI ⭐
### *Your Personal AI Operating System*

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![UI](https://img.shields.io/badge/UI-PyQt6-0073ec.svg?style=for-the-badge&logo=qt&logoColor=white)](https://www.qt.io/)
[![AI Engine](https://img.shields.io/badge/AI-Gemini%20Live-00ffff.svg?style=for-the-badge&logo=google-gemini&logoColor=white)](https://deepmind.google/technologies/gemini/)
[![Automation](https://img.shields.io/badge/Automation-Playwright%20%26%20PyAutoGUI-orange.svg?style=for-the-badge&logo=playwright&logoColor=white)](https://playwright.dev/)
[![OS Support](https://img.shields.io/badge/OS-Windows-0078d4.svg?style=for-the-badge&logo=windows&logoColor=white)](#)

**NEXUS AI** is an advanced Personal AI Operating System developed by **Ayushh**. It transforms your local computer into an intelligent, voice-activated partner. Built on the Gemini Live connection (providing low-latency native audio streaming) and integrated with local OS commands, vision modules, and autonomous developer tools, NEXUS AI bridges the gap between natural language reasoning and computer control.

NEXUS AI can control desktop environments, write and run software projects autonomously, retrieve real-time data, manage long-term personal memories, send automated chat messages, schedule reminders, and interact visually with what is displayed on your screen.

---

## ⚡ Key Features

NEXUS AI leverages custom action modules combined with a central planning and reasoning engine:

### 🎙️ Low-Latency Voice Connection
* **Native Audio Streaming**: Integrates Google's Live Connection (`models/gemini-2.5-flash-native-audio-preview-12-2025`) using `sounddevice` for bidirectional spoken conversations.
* **Custom Wake Words**: Activates selectively on phrase triggers (e.g., *"Hello Nexus AI"*, *"Nexus AI"*, *"Hello Nexus"*, or *"Nexus"*).

### 🖥️ Deep Computer Control & Desktop Automation
* **System Settings Manager**: Adjusts volume (via `pycaw`), changes screen brightness, scrolls windows, minimizes/maximizes active tabs, locks the computer, takes screenshots, restarts or shuts down the host PC.
* **Universal Application Launcher**: Launches arbitrary programs, applications, and folders using standard execution paths and user search emulation.

### 🧠 Dynamic Long-Term Memory
* **Context Extraction**: Automatically parses conversations for preferences, notes, relationships, wishes, and project contexts.
* **Structured Local Storage**: Keeps data updated in `memory/long_term.json` to feed back into future LLM prompt contexts for customized, persistent user interaction.

### 🔍 Real-Time Information & Scraping
* **Web Search Engine**: Utilizes custom search APIs and scrapers to pull current news, facts, comparisons, and comparisons of products.
* **Flight Finder & Weather Reports**: Resolves dates/locations dynamically, performs flight searches, and updates local weather interfaces.

### 🚀 Autonomous Developer Agent & Coding Suite
* **Dev Agent Core**: A self-contained builder that creates entire software projects locally. It builds, runs, compiles, inspects stdout/stderr, detects issues, writes fixes, and retries up to 5 times until the code runs correctly.
* **Code Assistant**: Supports code generation, editing, optimization, explanations, and automatic screen-debugging (taking screenshots of code/errors and proposing fixes).

### 💬 Messaging & Task Automation
* **Universal Message Dispatcher**: Uses coordinates-independent resolution and UI automation to open and send messages across WhatsApp and Instagram without relying on hardcoded click paths.
* **Task Scheduling**: Creates and monitors timed tasks directly via Windows Task Scheduler integration.

### 🎮 Game Library Management
* **Launcher Integration**: Detects Steam and Epic Games installations from the Windows Registry to automatically launch, install, or update games from your local library.

### 👁️ NEXUS Vision Mode
* **Aspect-Ratio-Safe Screen Preview**: Displays the live monitor stream keeping native monitor proportions without cropping, stretching, or squashing.
* **Dynamic Sidebar Widening**: Toggling the `[Expand]` option on the sidebar preview panel dynamically expands the left sidebar width from `250px` to `360px`, magnifying text details so VS Code filenames and browser tabs are visually recognizable.
* **Vision Center Page**: A dedicated workspace monitoring console that allocates **75-85%** of the page area to a large screen viewer.
* **Decoupled OCR Processing**: Offloads local winsdk OCR and AST analysis to an independent background worker thread (`OCRWorker`). Capture and rendering run smoothly at 10-15 FPS without stuttering on OCR latency.
* **Multi-Monitor Switcher**: Sleek dashboard selectors `[M1]` (Monitor 1), `[M2]` (Monitor 2), and `[ALL]` (All Displays/Virtual Desktop) switch inputs dynamically in real time without restarting.
* **Developer Workspace Monitor**: Automatically extracts active project context, current file, language, and compile errors/warnings when VS Code is in focus.
* **Self-Healing Watchdog**: A background daemon monitors and auto-heals capture loops and UI widgets.

---

## 🏗️ Project Architecture

```text
NEXUS-AI/
├── actions/                  # Modular task controllers
│   ├── browser_control.py    # Playwright browser automation
│   ├── code_helper.py        # Code editor, runner, and screen debugger
│   ├── computer_control.py   # Shell executor and system commands
│   ├── computer_settings.py  # System volume, brightness, OS power controls
│   ├── desktop.py            # PyAutoGUI controls and navigation helper
│   ├── dev_agent.py          # Self-correcting software development agent
│   ├── file_controller.py    # File read/write utilities
│   ├── file_processor.py     # Batch processing and document scanning
│   ├── flight_finder.py      # Real-time flight search automation
│   ├── game_updater.py       # Registry scanner and Epic/Steam installer
│   ├── open_app.py           # Application search and execution paths
│   ├── reminder.py           # Windows Task Scheduler manager
│   ├── screen_processor.py   # Screen capture, cv2 compression, and vision stream
│   ├── send_message.py       # WhatsApp & Instagram text automation
│   ├── weather_report.py     # Browser-based weather retrieval
│   ├── web_search.py         # DuckDuckGo and BeautifulSoup web scraping
│   └── youtube_video.py      # Video playback and transcript scraper
├── agent/                    # Intelligent core orchestrators
│   ├── error_handler.py      # Core exception analyzer and repair engine
│   ├── executor.py           # Plan step-executor and code runtime manager
│   ├── planner.py            # Plan generator for multi-step tasks
│   └── task_queue.py         # Sequential queue handling
├── core/                     # Prompt templates & identity definitions
│   └── prompt.txt            # System constraints, rules, and behaviors
├── memory/                   # Contextual user settings & persistence
│   ├── config_manager.py     # API key manager utility
│   ├── long_term.json        # Persistent JSON data (identity, preferences)
│   └── memory_manager.py     # Memory extraction and serialization module
├── ui.py                     # PyQt6 dark dashboard containing live resource metrics
├── main.py                   # Entry point initiating UI and Live Audio Connection
├── or_client.py              # OpenRouter API client with automatic free-model fallbacks
├── setup.py                  # Automatic dependency and Playwright browser installer
└── requirements.txt          # Python dependencies
```

---

## 🛠️ Technology Stack

NEXUS AI is built using the following core libraries and APIs:

* **Language**: Python 3.10+
* **GUI Framework**: PyQt6 (Futuristic, high-contrast dark theme with system metrics)
* **Speech & Audio**: `sounddevice`, `pyaudio` for low-latency microphone streaming
* **Vision & Media**: OpenCV (`cv2`), `Pillow` (PIL), and `mss` for high-speed screen capture
* **Browsing/Scraping**: `playwright`, `beautifulsoup4`, `duckduckgo-search`
* **Automation**: `pyautogui`, `pywinauto`, `pygetwindow`, `pyperclip`
* **OS & Hardware Integration**: `psutil` (system metrics), `pycaw` (audio endpoints), `comtypes`, `win10toast` (native Windows notifications)
* **API Providers**: Google Generative AI (Live Audio API) and OpenRouter (for free LLM text/vision fallbacks)

---

## 📦 Installation Guide

To run NEXUS AI locally, follow these steps:

### 1. Clone the Repository
```bash
git clone https://github.com/Ayush-0915/NEXUS-AI.git
cd NEXUS-AI
```

### 2. Run Setup Script
The `setup.py` script automatically installs all required Python dependencies and registers the necessary Playwright browsers:
```bash
python setup.py
```

*Alternatively, you can install them manually:*
```bash
pip install -r requirements.txt
playwright install
```

---

## ⚙️ Configuration

Before launching, configure your AI credentials. 

Create a file named `api_keys.json` in the `config/` directory:

```json
{
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "openrouter_api_key": "YOUR_OPENROUTER_API_KEY"
}
```

*Note: Make sure your `config/api_keys.json` remains ignored by Git to prevent public exposure of credentials.*

---

## 💡 Usage Examples

Run the main dashboard interface:
```bash
python main.py
```

Once online, you can control NEXUS AI using voice commands or the keyboard.

### Spoken or Written Commands
* **Opening Apps**: *"Open Google Chrome"* or *"Launch Spotify"*
* **Screen Understanding**: *"Analyze this"* or *"Read what is on my screen"*
* **Web Queries**: *"Search the web for the latest updates on space exploration"*
* **Productivity**: *"Schedule a reminder on 2026-10-15 at 09:30 to buy milk"*
* **Messaging**: *"Send a WhatsApp message to John saying I will be late"*
* **Development**: *"Create a new project that scrapes weather statistics"* or *"Build and fix this code"*
* **System Volume**: *"Set system volume to 50%"*

---

## 🖼️ Screenshots

### Main Interface
*Sleek, responsive dashboard showing system resources, prompt inputs, and active session streams.*
<img width="1919" height="1031" alt="image" src="https://github.com/user-attachments/assets/55289f22-13c9-4943-95c7-ce5e2e8bd4c0" />
<img width="343" height="661" alt="image" src="https://github.com/user-attachments/assets/b8d0baac-f920-4b04-9764-15a0590947b3" />




---

## 🗺️ Future Roadmap

* [ ] **Offline Memory Optimization**: Migrating to local vector embeddings for advanced long-term user queries.
* [ ] **Local LLM Execution**: Optional offline mode using llama.cpp or Ollama integrations.
* [ ] **Mobile Companion Companion**: Mobile app integration to control systems remotely.
* [ ] **Multi-Agent Collaboration**: Multi-step workflows delegating concurrent subprocesses to dedicated micro-agents.
* [ ] **Enhanced Computer Vision**: Active real-time screen detection loops for preemptive helper tasks.

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

# 👨‍💻 Author

### **Ayushh**
*Developer of NEXUS AI* ⭐

* **GitHub**: [Ayush-0915](https://github.com/Ayush-0915)
* **Project Repository**: [NEXUS-AI](https://github.com/Ayush-0915/NEXUS-AI)
