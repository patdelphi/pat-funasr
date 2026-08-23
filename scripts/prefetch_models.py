"""
程序说明：
模型预下载脚本（使用 FunASR AutoModel 触发下载并缓存到本地）。

用途：
- 在跑 "test" 目录批量测试前，先把模型文件下载到 "workspace/models"（优先使用 ModelScope 加速）。
- 关闭在线 update 检查（disable_update=True），减少卡顿与不确定性。

注意：
- 该脚本会访问模型仓库（外网），属于“下载模型”行为，请在网络可用时运行。
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path


_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from model_catalog import get_model_configs


MODEL_CONFIGS = get_model_configs(
    ("sensevoice", "paraformer", "fun-asr-nano", "qwen3-asr")
)


def _print(msg: str) -> None:
    sys.stdout.write(msg.rstrip("\n") + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models",
        default="sensevoice,paraformer,fun-asr-nano,qwen3-asr",
        help="逗号分隔的模型别名列表",
    )
    parser.add_argument("--device", default="cpu", help="预下载时的 device（建议 cpu）")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    os.environ.setdefault("MODELSCOPE_CACHE", str(repo / "workspace" / "models"))
    os.environ.setdefault("HF_HOME", str(repo / "workspace" / "models" / "huggingface"))

    want = [m.strip() for m in args.models.split(",") if m.strip()]
    for alias in want:
        if alias not in MODEL_CONFIGS:
            raise SystemExit(f"Unknown model alias: {alias}, allowed={list(MODEL_CONFIGS.keys())}")

    from funasr import AutoModel

    _print(f"MODELSCOPE_CACHE={os.environ.get('MODELSCOPE_CACHE','')}")
    _print(f"HF_HOME={os.environ.get('HF_HOME','')}")
    _print("")

    for alias in want:
        cfg = dict(MODEL_CONFIGS[alias])
        cfg["device"] = args.device
        cfg["disable_update"] = True
        _print(f"[PREFETCH] {alias} => {cfg.get('model')} (hub={cfg.get('hub')}, device={cfg.get('device')})")
        try:
            _ = AutoModel(**cfg)
            _print(f"[OK] {alias}")
        except Exception:
            _print(f"[FAIL] {alias}")
            for line in traceback.format_exception(*sys.exc_info()):
                _print(line.rstrip("\n"))
            return 2
        _print("")

    _print("[DONE] all models prefetched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
