"""
程序说明：
验证模型缺失时不会静默联网，模型自带 requirements 也不会默认安装。
"""

from __future__ import annotations

import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_APP_DIR = _ROOT / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))


def test_model_requirements_install_is_disabled_by_default(monkeypatch):
    from funasr.download import download_model_from_hub

    monkeypatch.delenv("FUNASR_ALLOW_MODEL_REQUIREMENTS_INSTALL", raising=False)

    assert not download_model_from_hub.model_requirements_install_allowed({})


def test_model_requirements_install_requires_explicit_opt_in(monkeypatch):
    from funasr.download import download_model_from_hub

    monkeypatch.setenv("FUNASR_ALLOW_MODEL_REQUIREMENTS_INSTALL", "1")

    assert download_model_from_hub.model_requirements_install_allowed({})
