"""
Config loading and project paths (flattened layout).

Keeps JSON configs optional and resilient to missing files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict
import json


def project_root() -> Path:
    """Return repository root (folder that contains configs/ and src/).

    Note:
    - This module lives at `src/settings/loader.py` now (nested two levels deep),
      so using `parents[1]` incorrectly points to `.../src` rather than the repo root.
    - We detect the root by walking upward looking for a directory that contains
      a `configs/` folder (and usually also `src/`). Fall back to `parents[2]`
      which is correct for the current layout.
    """
    here = Path(__file__).resolve()
    # Walk up a few levels to find a directory that has `configs/`
    for parent in here.parents:
        if (parent / "configs").exists():
            return parent
    # Fallback: two levels up from src/settings/loader.py -> project root
    try:
        return here.parents[2]
    except Exception:
        # Last resort: previous behaviour (often wrong in this layout)
        return here.parents[1]


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
        return {}


@dataclass
class ModelConfig:
    base_url: str = "https://api.moonshot.cn/v1"
    npc: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict) -> "ModelConfig":
        return ModelConfig(
            base_url=str(d.get("base_url", "https://api.moonshot.cn/v1")),
            npc=dict(d.get("npc") or {}),
        )


def load_model_config() -> ModelConfig:
    return ModelConfig.from_dict(load_json(configs_dir() / "model.json"))


def load_prompts() -> dict:
    return load_json(configs_dir() / "prompts.json")


def load_feature_flags() -> dict:
    return load_json(configs_dir() / "feature_flags.json")


def load_characters() -> dict:
    return load_json(configs_dir() / "characters.json")


def load_story_config() -> dict:
    story_path = configs_dir() / "story.json"
    data = load_json(story_path)
    if data:
        return data
    return load_json(project_root() / "docs" / "plot.story.json")


def load_weapons() -> dict:
    """Load weapons table; returns {} if file missing.

    Expected shape:
    {
      "weapon_id": { "reach_steps": int, "ability": "STR|DEX|...", "damage_expr": "1d6+STR", "proficient_default": bool }
    }
    """
    return load_json(configs_dir() / "weapons.json")
