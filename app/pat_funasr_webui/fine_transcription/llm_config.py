# -*- coding: utf-8 -*-
"""
LLM 配置读取模块
从 .env 文件读取多 provider/model 配置，提供前端可用 LLM 列表
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# .env 文件路径（项目根目录）
_ENV_PATH = Path(__file__).resolve().parent.parent.parent.parent / ".env"


@dataclass
class LLMConfig:
    """单个 LLM 配置项"""
    name: str            # 显示名称
    provider: str        # 服务类型: ollama/openai/claude/custom
    base_url: str        # API 地址
    api_key: str         # API Key
    models: list = field(default_factory=list)  # 可用模型列表
    enabled: bool = True  # 是否启用


def _parse_env() -> dict[int, LLMConfig]:
    """
    解析 .env 文件，提取 LLM 配置
    格式: LLM_<索引>_<字段>=值
    """
    configs = {}

    # 尝试读取 .env 文件
    env = {}
    if _ENV_PATH.exists():
        with open(_ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()

    # 兼容 os.environ（覆盖文件值）
    for k, v in os.environ.items():
        if k.startswith("LLM_") and v:
            env[k] = v

    # 提取索引
    indices = set()
    for key in env:
        parts = key.split("_")
        if len(parts) >= 2 and parts[1].isdigit():
            indices.add(int(parts[1]))

    for idx in sorted(indices):
        prefix = f"LLM_{idx}_"
        name = env.get(f"{prefix}NAME", f"LLM {idx}")
        provider = env.get(f"{prefix}PROVIDER", "custom")
        base_url = env.get(f"{prefix}BASE_URL", "http://127.0.0.1:11434/v1")
        api_key = env.get(f"{prefix}API_KEY", "no-key")
        models_str = env.get(f"{prefix}MODELS", "")
        models = [m.strip() for m in models_str.split(",") if m.strip()]
        enabled = env.get(f"{prefix}ENABLED", "true").lower() == "true"

        if enabled and models:
            configs[idx] = LLMConfig(
                name=name, provider=provider, base_url=base_url,
                api_key=api_key, models=models, enabled=enabled,
            )

    return configs


def get_available_llms() -> list[tuple[str, str, LLMConfig]]:
    """
    获取所有已配置且启用的 LLM 选项
    返回 [(display_label, model_name, LLMConfig), ...]
    """
    configs = _parse_env()
    options = []
    for idx, cfg in configs.items():
        for model in cfg.models:
            label = f"[{cfg.name}] {model}"
            options.append((label, model, cfg))
    return options


def get_llm_choices() -> list[tuple[str, str]]:
    """
    获取 Gradio Dropdown 选项
    返回 [(label, value), ...]，value 格式: "idx|model_name"
    """
    configs = _parse_env()
    choices = []
    for idx, cfg in configs.items():
        for model in cfg.models:
            label = f"[{cfg.name}] {model}"
            value = f"{idx}|{model}"
            choices.append((label, value))
    return choices


def get_llm_by_value(value: str) -> Optional[tuple[LLMConfig, str]]:
    """
    根据 Dropdown value 获取 LLM 配置和模型名
    value 格式: "idx|model_name"
    返回 (LLMConfig, model_name) 或 None
    """
    if not value or "|" not in value:
        return None
    idx_str, model = value.split("|", 1)
    try:
        idx = int(idx_str)
    except ValueError:
        return None
    configs = _parse_env()
    cfg = configs.get(idx)
    if cfg and model in cfg.models:
        return cfg, model
    return None


def get_default_llm_value() -> Optional[str]:
    """获取默认 LLM 选项值"""
    choices = get_llm_choices()
    return choices[0][1] if choices else None
