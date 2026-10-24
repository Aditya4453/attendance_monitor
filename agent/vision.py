from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional, Tuple
from loguru import logger
from PIL import Image
import ollama

from agent.models import AttendanceSummary


def resize_screenshot(image_path: Path, max_width: int = 1280) -> Path:
    """
    Shrinks an image using Pillow if its width exceeds max_width while preserving aspect ratio.
    Moondream works best with images around 1024-1280px wide — larger images don't improve
    accuracy but do slow down inference.
    Returns the path to the resized image (or the original if already within limits).
    """
    logger.info(f"Checking screenshot dimensions for: {image_path.name}")
    with Image.open(image_path) as img:
        width, height = img.size
        if width <= max_width:
            logger.info(f"Image width ({width}px) is within limit ({max_width}px). No resizing needed.")
            return image_path

        ratio = max_width / float(width)
        new_height = int(float(height) * ratio)
        logger.info(f"Resizing image from {width}x{height} to {max_width}x{new_height}")

        resized_img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

        resized_path = image_path.with_name(f"{image_path.stem}_resized{image_path.suffix}")
        resized_img.save(resized_path, quality=90, optimize=True)
        logger.info(f"Saved resized screenshot to: {resized_path.name}")
        return resized_path


def clean_markdown_fences(raw_text: str) -> str:
    """Removes ```json ... ``` markdown code fences to isolate the JSON object."""
    cleaned = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    else:
        json_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group(1).strip()
    return cleaned


def _extract_ollama_content(response: Any) -> str:
    """Safely extracts message content from Ollama response across SDK versions."""
    if hasattr(response, "message") and hasattr(response.message, "content"):
        return str(response.message.content or "").strip()
    if isinstance(response, dict):
        msg = response.get("message", {})
        if isinstance(msg, dict):
            return str(msg.get("content", "")).strip()
        if hasattr(msg, "content"):
            return str(getattr(msg, "content", "")).strip()
    return str(response).strip()


def _build_moondream_prompt() -> str:
    """
    Returns a structured question prompt optimised for moondream.
    Moondream responds better to a direct visual question than a
    bare JSON schema instruction — we ask it to describe the data
    naturally, then parse the numbers ourselves.
    """
    return (
        "This is a period-wise attendance page from a student portal. "
        "List every subject you can see and its attendance percentage. "
        "Also tell me the overall attendance percentage, total classes, "
        "total attended, and total absent if shown. "
        "For each subject state whether it is below 75% (needs attention) "
        "or 75% and above (good). "
        "Reply ONLY with a JSON object in exactly this format, no extra text:\n"
        '{"overall_percentage": <number or "N/A">, '
        '"total_classes": <number or 0>, '
        '"attended": <number or 0>, '
        '"absent": <number or 0>, '
        '"subjects": [{"name": "<subject>", "percentage": <number>, "attended": <number>, "total": <number>}], '
        '"low_attendance_subjects": ["<subject names under 75%>"], '
        '"one_line_summary": "<one sentence summary>"}'
    )


def analyze_attendance_screenshot(
    image_path: Path,
    model_name: str = "moondream",
    max_width: int = 1280,
) -> Tuple[Optional[AttendanceSummary], str]:
    """
    Resizes the screenshot and sends it to the local Ollama vision model (moondream by default)
    to extract structured attendance data.
    Returns a tuple of (AttendanceSummary | None, raw_response_string).
    """
    resized_path = resize_screenshot(image_path, max_width=max_width)

    prompt = _build_moondream_prompt()
    logger.info(f"Sending screenshot to Ollama model '{model_name}'...")

    messages = [
        {
            "role": "user",
            "content": prompt,
            "images": [str(resized_path.resolve())],
        }
    ]

    try:
        response = ollama.chat(
            model=model_name,
            messages=messages,
        )
        raw_text = _extract_ollama_content(response)
        logger.debug(f"Raw model response: {raw_text}")
    except Exception as e:
        logger.error(f"Failed to communicate with Ollama: {e}")
        raise RuntimeError(
            f"Ollama call failed for model '{model_name}'. "
            f"Ensure Ollama is running ('ollama serve') and model is pulled "
            f"('ollama pull {model_name}'). Original error: {e}"
        ) from e

    # Attempt 1: direct JSON parse + Pydantic validation
    parsed_model = _try_parse_and_validate(raw_text)
    if parsed_model:
        logger.info("Successfully parsed and validated attendance summary.")
        return parsed_model, raw_text

    # Attempt 2: ask the model to reformat its own answer as clean JSON
    logger.warning("First parsing attempt failed. Retrying with clean-JSON prompt...")
    retry_prompt = (
        "Convert the following text into a valid JSON object with these exact keys: "
        "overall_percentage, total_classes, attended, absent, "
        "subjects (list of {name, percentage, attended, total}), "
        "low_attendance_subjects (list of strings), one_line_summary. "
        "Reply ONLY with the raw JSON — no markdown, no explanation.\n\n"
        f"Text to convert:\n{raw_text}"
    )

    try:
        retry_response = ollama.chat(
            model=model_name,
            messages=[{"role": "user", "content": retry_prompt}],
        )
        retry_text = _extract_ollama_content(retry_response)
        parsed_retry = _try_parse_and_validate(retry_text)
        if parsed_retry:
            logger.info("Successfully parsed attendance summary on retry.")
            return parsed_retry, retry_text
        else:
            logger.warning("Retry parsing also failed. Falling back to raw text output.")
            return None, raw_text
    except Exception as e:
        logger.warning(f"Retry request failed: {e}. Falling back to raw text.")
        return None, raw_text


def _try_parse_and_validate(text: str) -> Optional[AttendanceSummary]:
    """Strips markdown fences, JSON-loads, and validates against the Pydantic schema."""
    try:
        clean_json_str = clean_markdown_fences(text)
        data = json.loads(clean_json_str)
        if isinstance(data, dict):
            return AttendanceSummary.model_validate(data)
    except Exception as exc:
        logger.debug(f"JSON validation error: {exc}")
    return None
