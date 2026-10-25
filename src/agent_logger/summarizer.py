"""Local model-powered summary generation with fallback.

This module attempts to use a local open-weight model (via Ollama) to generate
concise session summaries from activity and diff data. If the model is unavailable,
it falls back to a deterministic rule-based summary.

The model name is configurable; the default is set to an Ollama model that should
be available after `ollama pull`. A fallback summary is always provided when the
model cannot be reached, ensuring the feature works offline.
"""

import json
import os
from typing import Optional, Dict, Any, List


# Default model configuration
DEFAULT_MODEL = os.environ.get("AGENT_LOGGER_MODEL", "phi")
"""Default model name. Set AGENT_LOGGER_MODEL=ollama:phi or similar to override."""

FALLBACK_TEMPLATE = (
    "Session summary: {activity_count} activity events across {file_count} file(s). "
    "Changes: {change_description}. Repository: {repo_path} on {branch}."
)
"""Deterministic fallback summary template."""


def _try_ollama(model_name: str, prompt: str, timeout: int = 15) -> Optional[str]:
    """Try to get a summary from Ollama.

    Returns the model's response text, or None if Ollama is unavailable/unhealthy.
    """
    import httpx

    base_url = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    url = f"{base_url}/api/generate"

    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 200,
        },
    }

    try:
        resp = httpx.post(url, json=payload, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("response")
        else:
            return None
    except Exception:
        return None


def _try_import_llama_cpp():
    """Try to import a local llama-cpp-python runtime.

    Returns the import module if available, None otherwise.
    """
    try:
        import llama_cpp

        return llama_cpp
    except ImportError:
        return None


def generate_summary(
    activity_count: int,
    file_count: int,
    changed_files: List[str],
    new_files: List[str],
    repo_path: str,
    branch: str,
    model_name: Optional[str] = None,
) -> str:
    """Generate a concise session summary.

    Tries a local open-weight model first (Ollama, llama-cpp), then falls back
    to a deterministic rule-based summary. The model is optional — if neither
    is available, the fallback is returned.

    Args:
        activity_count: Number of activity events recorded during the session.
        file_count: Number of unique files changed or created.
        changed_files: List of files that were modified.
        new_files: List of newly created files.
        repo_path: Path to the repository.
        branch: Git branch name.
        model_name: Optional model name override.

    Returns:
        A concise summary string.
    """
    model_name = model_name or DEFAULT_MODEL

    # Prepare the diff activity payload for the model
    changed_str = ", ".join(changed_files[:10]) if changed_files else "no changes"
    new_str = ", ".join(new_files[:10]) if new_files else "no new files"
    activity_desc = (
        f"{activity_count} events, {file_count} files"
        if activity_count and file_count
        else f"{activity_count or 0} events"
    )

    # Build the prompt
    prompt = f"""You are a coding-session summarizer. Given the activity and git diff below, produce a concise 2-sentence summary of what the agent accomplished.

Repository: {repo_path} (branch: {branch})
Activity: {activity_desc}
Changed files: {changed_str}
New files: {new_str}

Summarize in 2 sentences, focusing on what code was changed/created and the likely purpose. Keep it under 100 words.
"""

    # Try Ollama first
    if model_name and model_name.startswith("ollama:"):
        ollama_model = model_name.split(":", 1)[1]
        result = _try_ollama(ollama_model, prompt, timeout=20)
        if result and result.strip():
            return result.strip()

    # Try llama-cpp-python if available
    llm_module = _try_import_llama_cpp()
    if llm_module is not None:
        try:
            # Check for a local model path
            ckpt_path = os.environ.get(
                "AGENT_LOGGER_LLM_CPP_MODEL",
                "/Users/rathnam.s/.ollama/models/phi/Phi-2.gguf",
            )
            if os.path.exists(ckpt_path):
                Llama = llm_module.Llama

                n_ctx = 512
                n_gpu_layers = int(os.environ.get("AGENT_LOGGER_N_GPU_LAYERS", "0"))
                llm = Llama(
                    model_path=ckpt_path,
                    n_ctx=n_ctx,
                    n_gpu_layers=n_gpu_layers,
                    verbose=False,
                )
                output = llm(
                    prompt,
                    max_tokens=200,
                    temperature=0.3,
                    stop=["\n\n", "Repository:", "Activity:"],
                )
                generated = output["choices"][0]["text"].strip()
                if generated:
                    return generated
        except Exception as e:
            # Surface the failure for debugging, then fall through to fallback.
            import sys

            print(f"agent-logger: llama-cpp summarization failed: {e}", file=sys.stderr)

    # Fallback: deterministic summary
    return _fallback_summary(
        activity_count, file_count, changed_files, new_files, repo_path, branch
    )


def _fallback_summary(
    activity_count: int,
    file_count: int,
    changed_files: List[str],
    new_files: List[str],
    repo_path: str,
    branch: str,
) -> str:
    """Deterministic fallback summary when no model is available."""

    changed_desc = ", ".join(changed_files[:5]) if changed_files else "no modifications"
    new_desc = ", ".join(new_files[:5]) if new_files else "no new files"

    n_changed = len(changed_files)
    n_new = len(new_files)

    if n_changed > 0 and n_new > 0:
        summary = f"Modified {n_changed} files and created {n_new} new files"
    elif n_changed > 0:
        summary = f"Modified {n_changed} file{'s' if n_changed != 1 else ''}"
    elif n_new > 0:
        summary = f"Created {n_new} new file{'s' if n_new != 1 else ''}"
    else:
        summary = "No code changes recorded"

    repo_info = f"Repository: {repo_path} on {branch}" if repo_path and branch else ""

    parts = [f"Session summary: {summary}."]
    if activity_count and activity_count > 0:
        parts.append(f"Activity: {activity_count} event{'s' if activity_count != 1 else ''}.")
    if repo_info:
        parts.append(repo_info)

    return " ".join(parts)