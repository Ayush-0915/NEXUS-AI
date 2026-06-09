# scratch/test_voice_pipeline.py
import sys
import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

# Mock sounddevice before importing main
class MockRawOutputStream:
    def __init__(self, *args, **kwargs):
        pass
    def start(self):
        pass
    def stop(self):
        pass
    def close(self):
        pass
    def write(self, data):
        pass

class MockInputStream:
    def __init__(self, *args, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

import sounddevice as sd
sd.RawOutputStream = MockRawOutputStream
sd.InputStream = MockInputStream

# Import main.py components
import main
from main import NexusLive

class MockUI:
    def __init__(self):
        self.muted = False
        self.on_text_command = None
    def set_state(self, state):
        print(f"[TEST UI STATE] -> {state}")
    def write_log(self, text):
        print(f"[TEST UI LOG] {text}")

async def run_voice_tests():
    print("=" * 60)
    print(" NEXUS AI — Harden Voice Pipeline Verification")
    print("=" * 60)
    
    ui = MockUI()
    nexus = NexusLive(ui)
    nexus.audio_in_queue = asyncio.Queue()
    nexus.out_queue = asyncio.Queue()
    nexus._loop = asyncio.get_event_loop()
    nexus.session = AsyncMock()

    print("\n--- Test 1: Single Audio Query Lifecycle ---")
    # Simulate a single turn
    # 1. Output transcription begins
    print("\n[Simulation] Assistant starts speaking...")
    nexus.set_speaking(True)
    
    # 2. Chunks received
    print("\n[Simulation] Audio chunks received...")
    nexus.audio_in_queue.put_nowait(b"\x00" * 1024)
    nexus.audio_in_queue.put_nowait(b"\x00" * 1024)
    
    # 3. Turn complete
    print("\n[Simulation] Turn complete received...")
    print("\n[AUDIO]\nTurn complete received\n")
    nexus.audio_in_queue.put_nowait(None)
    print("\n[AUDIO]\nSentinel queued\n")
    
    # 4. Run playback loop tasks briefly to clear queue
    print("\n[Simulation] Playing audio chunks...")
    play_task = asyncio.create_task(nexus._play_audio())
    await asyncio.sleep(0.5)
    play_task.cancel()
    
    print("\nTest 1 check: Is speaking =", nexus._is_speaking)
    assert not nexus._is_speaking, "Error: Nexus Live should have returned to LISTENING"
    print("Status: SUCCESS OK")

    print("\n--- Test 2: Failsafe Timeout Verification ---")
    print("\n[Simulation] Simulating stuck SPEAKING state...")
    # Set speaking to True without sending sentinel (stuck)
    nexus.set_speaking(True)
    
    # Run failsafe monitor briefly
    failsafe_task = asyncio.create_task(nexus._failsafe_monitor())
    
    print("Waiting for 1.0s (failsafe threshold modified for test)...")
    # Speed up failsafe check: modify speaking_start_time so it triggers immediately
    nexus.speaking_start_time = time.time() - 12.0
    
    await asyncio.sleep(1.0)
    failsafe_task.cancel()
    
    print("\nTest 2 check: Is speaking =", nexus._is_speaking)
    assert not nexus._is_speaking, "Error: Failsafe should have forced back to LISTENING"
    print("Status: SUCCESS OK")

    print("\n--- Test 3: 10 Consecutive Commands Sequence ---")
    commands = [
        "Hello",
        "What time is it?",
        "Tell me a joke",
        "Explain AI",
        "Open calculator",
        "What are my laptop specs?",
        "Analyze my screen",
        "Open notepad",
        "Tell me a fact",
        "Goodbye"
    ]
    
    for i, cmd in enumerate(commands, 1):
        print(f"\n[Command {i}] Voice: '{cmd}'")
        # Start speaking simulation
        nexus.set_speaking(True)
        # Feed chunks
        nexus.audio_in_queue.put_nowait(b"\x00" * 512)
        nexus.audio_in_queue.put_nowait(None)
        
        # Play chunks
        play_task = asyncio.create_task(nexus._play_audio())
        await asyncio.sleep(0.1)
        play_task.cancel()
        
        print(f"Command {i} check: Is speaking =", nexus._is_speaking)
        assert not nexus._is_speaking, f"Error: Command {i} left speaking state active"
        
    print("\nTest 3: All 10 consecutive commands simulated successfully!")
    print("Status: SUCCESS OK")
    print("\n" + "=" * 60)

if __name__ == "__main__":
    asyncio.run(run_voice_tests())
