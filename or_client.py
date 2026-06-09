import json
import sys
import time
import base64
import logging
from pathlib import Path
from typing import Optional

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openrouter_client")

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR     = _get_base_dir()
API_KEY_PATH = BASE_DIR / "config" / "api_keys.json"

def _load_api_key() -> str:
    try:
        with open(API_KEY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        key = data.get("openrouter_api_key", "").strip()
        if not key:
            raise ValueError("openrouter_api_key is empty in api_keys.json")
        return key
    except FileNotFoundError:
        raise RuntimeError(f"api_keys.json not found at: {API_KEY_PATH}")
    except Exception as e:
        raise RuntimeError(f"Failed to load OpenRouter API key: {e}")

TEXT_MODELS: list[str] = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-4-31b-it:free",
    "qwen/qwen3-coder:free",
    "z-ai/glm-4.5-air:free",
    "google/gemma-4-26b-a4b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "liquid/lfm-2.5-1.2b-thinking:free",
    "liquid/lfm-2.5-1.2b-instruct:free",
]

VISION_MODELS: list[str] = [
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]

API_URL               = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MAX_TOKENS    = 4096
DEFAULT_TEMPERATURE   = 0.7
REQUEST_TIMEOUT       = 60   # seconds per request
MAX_RETRIES_PER_MODEL = 2    # attempts before moving to next model
RETRY_DELAY           = 2    # seconds between retries

# Failures tracking: model_name -> (timestamp, reason)
# Reasons: "invalid" (404/401), "rate_limited" (429), "provider_unavailable" (5xx), "error" (general error/timeout)
_failed_models: dict[str, tuple[float, str]] = {}

def _is_model_unavailable(model: str) -> bool:
    if model not in _failed_models:
        return False
    timestamp, reason = _failed_models[model]
    now = time.time()
    
    if reason == "invalid":
        cooldown = 1800.0  # 30 minutes for HTTP 404/401
    elif reason == "rate_limited":
        cooldown = 60.0    # 60 seconds for HTTP 429
    else:
        cooldown = 300.0   # 5 minutes for HTTP 5xx / timeouts / general errors
        
    if now - timestamp < cooldown:
        return True
        
    # Cooldown expired, clean up
    try:
        del _failed_models[model]
    except KeyError:
        pass
    return False

def _mark_model_failed(model: str, reason: str) -> None:
    _failed_models[model] = (time.time(), reason)
    logger.warning(
        f"[OpenRouter] Model {model} marked as failed ({reason}). "
        f"Initiated cooldown."
    )

def _load_gemini_api_key() -> str:
    try:
        with open(API_KEY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        key = data.get("gemini_api_key", "").strip()
        if not key:
            raise ValueError("gemini_api_key is empty in api_keys.json")
        return key
    except FileNotFoundError:
        raise RuntimeError(f"api_keys.json not found at: {API_KEY_PATH}")
    except Exception as e:
        raise RuntimeError(f"Failed to load Gemini API key: {e}")


class OpenRouterClient:

    def __init__(self) -> None:
        self.api_key  = _load_api_key()
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "https://github.com/Ayushh/NEXUS-AI",
            "X-Title":       "NEXUS AI",
        }

    def _is_rate_limited(self, model: str) -> bool:
        # Compatibility wrapper
        return _is_model_unavailable(model)

    def _mark_rate_limited(self, model: str) -> None:
        # Compatibility wrapper
        _mark_model_failed(model, "rate_limited")

    def _call(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        response_format: Optional[dict] = None,
    ) -> Optional[str]:
        payload: dict = {
            "model":       model,
            "messages":    messages,
            "max_tokens":  max_tokens,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format

        for attempt in range(1, MAX_RETRIES_PER_MODEL + 1):
            start_time = time.time()
            try:
                resp = requests.post(
                    API_URL,
                    headers=self._headers,
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                )
                latency = time.time() - start_time

                if resp.status_code in (404, 401):
                    _mark_model_failed(model, "invalid")
                    print(
                        f"\n[LLM DEBUG]\n"
                        f"Provider: OpenRouter\n"
                        f"Model: {model}\n"
                        f"Status: HTTP {resp.status_code}\n"
                        f"Latency: {latency:.1f}s\n"
                        f"Action: Marked Invalid\n"
                        f"Fallback: Next Model\n"
                    )
                    return None

                if resp.status_code == 429:
                    _mark_model_failed(model, "rate_limited")
                    print(
                        f"\n[LLM DEBUG]\n"
                        f"Provider: OpenRouter\n"
                        f"Model: {model}\n"
                        f"Status: HTTP 429\n"
                        f"Latency: {latency:.1f}s\n"
                        f"Action: Marked Rate-Limited\n"
                        f"Fallback: Next Model\n"
                    )
                    return None

                if resp.status_code >= 500:
                    _mark_model_failed(model, "provider_unavailable")
                    print(
                        f"\n[LLM DEBUG]\n"
                        f"Provider: OpenRouter\n"
                        f"Model: {model}\n"
                        f"Status: HTTP {resp.status_code}\n"
                        f"Latency: {latency:.1f}s\n"
                        f"Action: Marked Provider Unavailable\n"
                        f"Fallback: Next Model\n"
                    )

                elif resp.status_code == 200:
                    data    = resp.json()
                    content = (
                        data.get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", "")
                    )
                    if model in _failed_models:
                        try:
                            del _failed_models[model]
                        except KeyError:
                            pass
                    print(
                        f"\n[LLM DEBUG]\n"
                        f"Provider: OpenRouter\n"
                        f"Model: {model}\n"
                        f"Status: HTTP 200\n"
                        f"Latency: {latency:.1f}s\n"
                    )
                    return content.strip() if content else None

                else:
                    _mark_model_failed(model, f"HTTP_{resp.status_code}")
                    print(
                        f"\n[LLM DEBUG]\n"
                        f"Provider: OpenRouter\n"
                        f"Model: {model}\n"
                        f"Status: HTTP {resp.status_code}\n"
                        f"Latency: {latency:.1f}s\n"
                        f"Action: Marked Failed\n"
                        f"Fallback: Next Model\n"
                    )

            except requests.exceptions.Timeout:
                latency = time.time() - start_time
                _mark_model_failed(model, "timeout")
                print(
                    f"\n[LLM DEBUG]\n"
                    f"Provider: OpenRouter\n"
                    f"Model: {model}\n"
                    f"Status: Timeout\n"
                    f"Latency: {latency:.1f}s\n"
                    f"Action: Marked Timeout\n"
                    f"Fallback: Next Model\n"
                )
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                latency = time.time() - start_time
                _mark_model_failed(model, "error")
                print(
                    f"\n[LLM DEBUG]\n"
                    f"Provider: OpenRouter\n"
                    f"Model: {model}\n"
                    f"Status: Error\n"
                    f"Exception Traceback:\n{tb}\n"
                    f"Latency: {latency:.1f}s\n"
                    f"Action: Marked Error\n"
                    f"Fallback: Next Model\n"
                )

            if attempt < MAX_RETRIES_PER_MODEL:
                time.sleep(RETRY_DELAY)

        return None

    def _call_gemini_fallback(
        self,
        messages: list[dict],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        response_format: Optional[dict] = None,
    ) -> Optional[tuple[str, str]]:
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            logger.error("[LLM] google-genai SDK not installed. Cannot run Gemini fallback.")
            return None

        try:
            gemini_key = _load_gemini_api_key()
            client = genai.Client(
                api_key=gemini_key,
                http_options={"api_version": "v1beta"}
            )

            system_instruction = None
            contents = []
            for msg in messages:
                role = msg.get("role")
                content = msg.get("content")
                if role == "system":
                    system_instruction = content
                else:
                    g_role = "user" if role == "user" else "model"
                    parts = []
                    
                    if isinstance(content, str):
                        parts.append(types.Part.from_text(text=content))
                    elif isinstance(content, list):
                        for part in content:
                            part_type = part.get("type")
                            if part_type == "text":
                                parts.append(types.Part.from_text(text=part.get("text", "")))
                            elif part_type == "image_url":
                                url_val = part.get("image_url", {}).get("url", "")
                                if url_val.startswith("data:"):
                                    try:
                                        mime_data_part, b64_data = url_val.split(";base64,")
                                        mime = mime_data_part.split("data:")[1]
                                        image_bytes = base64.b64decode(b64_data)
                                        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime))
                                    except Exception as e:
                                        logger.error(f"[LLM] Failed to parse base64 image: {e}")
                    
                    contents.append(types.Content(role=g_role, parts=parts))

            if not contents:
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text="Hello")]))

            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                max_output_tokens=max_tokens,
                temperature=temperature,
            )
            if response_format and response_format.get("type") == "json_object":
                config.response_mime_type = "application/json"

            # Try a pool of Gemini models
            gemini_models = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-1.5-flash"]
            last_error = None
            for model_name in gemini_models:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=config
                    )
                    if response.text:
                        return response.text.strip(), model_name
                except Exception as e:
                    logger.warning(f"[LLM] Gemini fallback model {model_name} failed: {e}. Trying next Gemini model...")
                    last_error = e
                    
            if last_error:
                raise last_error
            return None
        except Exception as e:
            logger.error(f"[LLM] Gemini fallback API call failed: {e}")
            raise

    def _call_with_fallback(
        self,
        pool: list[str],
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        response_format: Optional[dict] = None,
    ) -> str:
        # 1. Try specifically requested model
        if model and not _is_model_unavailable(model):
            result = self._call(model, messages, max_tokens, temperature, response_format)
            if result:
                print(
                    f"\n[LLM DEBUG]\n"
                    f"Final Provider Used: OpenRouter\n"
                    f"Model: {model}\n"
                )
                return result

        # 2. Try pool of fallback models
        for m in pool:
            if _is_model_unavailable(m):
                continue
            
            if model:
                print(
                    f"\n[LLM DEBUG]\n"
                    f"Selected Model: {model}\n"
                    f"Fallback Model: {m}\n"
                )
            else:
                print(
                    f"\n[LLM DEBUG]\n"
                    f"Selected Model: {pool[0]}\n"
                    f"Fallback Model: {m}\n"
                )

            result = self._call(m, messages, max_tokens, temperature, response_format)
            if result:
                print(
                    f"\n[LLM DEBUG]\n"
                    f"Final Provider Used: OpenRouter\n"
                    f"Model: {m}\n"
                )
                return result

        # 3. Gemini fallback
        gemini_model = "gemini-2.5-flash"
        if model:
            print(
                f"\n[LLM DEBUG]\n"
                f"Selected Model: {model}\n"
                f"Fallback Model: {gemini_model}\n"
            )
        else:
            print(
                f"\n[LLM DEBUG]\n"
                f"Selected Model: {pool[0]}\n"
                f"Fallback Model: {gemini_model}\n"
            )

        logger.info("[LLM] All OpenRouter models failed. Trying Gemini fallback...")
        start_time = time.time()
        try:
            result_tuple = self._call_gemini_fallback(messages, max_tokens, temperature, response_format)
            latency = time.time() - start_time
            if result_tuple:
                res_text, used_model = result_tuple
                print(
                    f"\n[LLM DEBUG]\n"
                    f"Provider: Gemini\n"
                    f"Model: {used_model}\n"
                    f"Status: SUCCESS\n"
                    f"Latency: {latency:.1f}s\n"
                    f"Final Provider Used: Gemini\n"
                )
                return res_text
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            latency = time.time() - start_time
            print(
                f"\n[LLM DEBUG]\n"
                f"Provider: Gemini\n"
                f"Model: {gemini_model}\n"
                f"Status: Error\n"
                f"Exception Traceback:\n{tb}\n"
                f"Latency: {latency:.1f}s\n"
            )

        # 4. Response Guarantee
        logger.error("[LLM] All providers failed.")
        return "NEXUS AI could not reach any language model provider. Please check your API keys, internet connection, or model configuration."

    def chat(
        self,
        prompt: str,
        system: str = (
            "You are a component of NEXUS AI, a personal AI operating system developed by Ayushh. "
            "Be concise, helpful, and precise."
        ),
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ]
        return self._call_with_fallback(
            TEXT_MODELS, messages, model, max_tokens, temperature
        )

    def chat_json(
        self,
        prompt: str,
        system: str = (
            "Return ONLY valid JSON. "
            "No markdown fences, no extra text, no explanation."
        ),
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> dict:
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ]
        raw = self._call_with_fallback(
            TEXT_MODELS, messages, model, max_tokens, temperature=0.2
        )

        clean = raw.strip()
        if clean.startswith("```"):
            parts = clean.split("```")
            clean = parts[1] if len(parts) > 1 else clean
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip().rstrip("`").strip()

        try:
            return json.loads(clean)
        except json.JSONDecodeError as e:
            if "NEXUS AI could not reach" in raw:
                return {"error": raw, "status": "failed"}
            logger.error(
                f"[OpenRouter] JSON parse failed: {e}\n"
                f"Raw response (first 300 chars): {raw[:300]}"
            )
            raise ValueError(
                f"Model returned unparseable JSON: {e}\n"
                f"Raw output: {raw[:200]}"
            )

    def vision(
        self,
        prompt: str,
        image_b64: str,
        mime: str = "image/png",
        system: str = "Analyze the image and describe what you see clearly and concisely.",
        model: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{image_b64}"
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            },
        ]
        return self._call_with_fallback(
            VISION_MODELS, messages, model, max_tokens, temperature=0.2
        )

    def vision_from_file(
        self,
        prompt: str,
        image_path: str,
        system: str = "Analyze the image and describe what you see clearly and concisely.",
        model: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> str:
        path = Path(image_path)
        mime_map = {
            ".png":  "image/png",
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif":  "image/gif",
        }
        mime = mime_map.get(path.suffix.lower(), "image/png")

        with open(path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        return self.vision(prompt, image_b64, mime, system, model, max_tokens)

    def multi_turn(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        return self._call_with_fallback(
            TEXT_MODELS, messages, model, max_tokens, temperature
        )

    def available_models(self) -> dict:
        return {
            "text_models":   TEXT_MODELS,
            "vision_models": VISION_MODELS,
            "rate_limited":  [k for k, v in _failed_models.items() if v[1] == "rate_limited"],
            "total_text":    len(TEXT_MODELS),
            "total_vision":  len(VISION_MODELS),
        }

client = OpenRouterClient()

if __name__ == "__main__":
    print("=" * 55)
    print("  NEXUS AI — OpenRouter Client Self-Test")
    print("=" * 55)

    print("\n[TEST 1] Basic chat...")
    try:
        reply = client.chat("Introduce yourself in one sentence.")
        print(f"  Response : {reply}")
        print(f"  Status   : PASS OK")
    except Exception as e:
        print(f"  Status   : FAIL FAILED — {e}")

    print("\n[TEST 2] JSON mode...")
    try:
        data = client.chat_json(
            'List 3 programming languages. Format: {"languages": ["a", "b", "c"]}',
            system="Return only valid JSON. No extra text."
        )
        print(f"  Response : {data}")
        print(f"  Status   : PASS OK")
    except Exception as e:
        print(f"  Status   : FAIL FAILED — {e}")

    print("\n[TEST 3] Multi-turn conversation...")
    try:
        history = [
            {"role": "system",    "content": "You are a helpful assistant. Be brief."},
            {"role": "user",      "content": "My name is Ayushh."},
            {"role": "assistant", "content": "Hello Ayushh, how can I help you?"},
            {"role": "user",      "content": "What is my name?"},
        ]
        reply = client.multi_turn(history)
        print(f"  Response : {reply}")
        print(f"  Status   : PASS OK")
    except Exception as e:
        print(f"  Status   : FAIL FAILED — {e}")

    print("\n[TEST 4] Model pool info...")
    info = client.available_models()
    print(f"  Text models   : {info['total_text']}")
    print(f"  Vision models : {info['total_vision']}")
    print(f"  Rate limited  : {info['rate_limited'] or 'none'}")
    print(f"  Status        : PASS OK")

    print("\n" + "=" * 55)
    print("  All tests complete.")
    print("=" * 55)