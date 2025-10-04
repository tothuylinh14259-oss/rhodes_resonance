"""
Config loading and project paths.

Keeps JSON configs optional and resilient to missing files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
import json


def project_root() -> Path:
    """Return repository root (folder that contains configs/ and src/)."""
    # This file lives at src/npc_talk/config.py
    return Path(__file__).resolve().parents[2]


def configs_dir() -> Path:
    return project_root() / "configs"


def load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data or {}
    except FileNotFoundError:
        return {}
    except Exception:
        # Be permissive: return empty dict on malformed content for now
        return {}


@dataclass
class ModelConfig:
    base_url: str = "https://api.moonshot.cn/v1"
    npc: Dict[str, Any] = field(default_factory=dict)
    narration: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict) -> "ModelConfig":
        return ModelConfig(
            base_url=str(d.get("base_url", "https://api.moonshot.cn/v1")),
            npc=dict(d.get("npc") or {}),
            narration=dict(d.get("narration") or {}),
        )


def load_model_config() -> ModelConfig:
    return ModelConfig.from_dict(load_json(configs_dir() / "model.json"))


def load_prompts() -> dict:
    """Optional prompts; may be absent. Returns a dict.

    Expected keys (all optional):
      - npc_prompt_template: str|list[str]
      - enemy_prompt_template: str|list[str]
      - name_map: dict
      - player_persona: str
    """
    return load_json(configs_dir() / "prompts.json")


def load_narration_policy() -> dict:
    return load_json(configs_dir() / "narration_policy.json")


def load_narration_env() -> dict:
    return load_json(configs_dir() / "narration_env.json")


def load_feature_flags() -> dict:
    return load_json(configs_dir() / "feature_flags.json")


def load_characters() -> dict:
    return load_json(configs_dir() / "characters.json")

