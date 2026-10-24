"""
agent/voice.py
--------------
Text-to-speech via the ElevenLabs Python SDK.

The API key is read from ELEVENLABS_API_KEY in .env — never put it in code.
If the key is absent the function is a silent no-op so the attendance flow
never crashes because of a missing voice configuration.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger


def speak(text: str) -> None:
    """
    Convert *text* to speech with ElevenLabs and play it through the default
    audio device.

    Reads from .env (two directories up from this file):
        ELEVENLABS_API_KEY   — required; silently skipped if absent
        ELEVENLABS_VOICE_ID  — optional; defaults to "Rachel"
    """
    # Load .env so the SDK can pick up ELEVENLABS_API_KEY
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

    api_key  = os.getenv("ELEVENLABS_API_KEY", "").strip()
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip() or "Rachel"

    if not api_key:
        logger.debug("ELEVENLABS_API_KEY not set — skipping voice output.")
        return

    try:
        from elevenlabs import play
        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=api_key)

        logger.info(f"Sending text to ElevenLabs (voice={voice_id})...")
        audio = client.text_to_speech.convert(
            text=text,
            voice_id=voice_id,
            model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
        )

        play(audio)
        logger.info("Voice playback complete.")

    except ImportError:
        logger.debug(
            "elevenlabs package not found — run 'pip install elevenlabs' to enable voice."
        )
    except Exception as exc:
        # Never let a voice failure crash the attendance flow
        logger.error(f"Voice output failed: {exc}")
