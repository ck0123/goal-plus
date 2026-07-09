from __future__ import annotations

import os


def configure_single_thread() -> None:
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
    os.environ["NUMEXPR_NUM_THREADS"] = "1"
    os.environ["MAX_JOBS"] = "1"


def configure_torch_threads(torch_module) -> None:
    torch_module.set_num_threads(1)
    try:
        torch_module.set_num_interop_threads(1)
    except RuntimeError:
        # PyTorch allows interop threads to be set only before parallel work.
        pass
