# -*- coding: utf-8 -*-
"""GPU detection helper for startup scripts - avoids cmd.exe quote nesting hell.

Prints: <GPU_MODEL_NAME>  (one line, to stdout)
Exit 0 if CUDA GPU detected, 1 otherwise.
"""
import sys
try:
    import torch
    if torch.cuda.is_available():
        print(torch.cuda.get_device_name(0))
        sys.exit(0)
    else:
        print("No CUDA", file=sys.stderr)
        sys.exit(1)
except Exception as e:
    print(f"torch import failed: {e}", file=sys.stderr)
    sys.exit(1)
