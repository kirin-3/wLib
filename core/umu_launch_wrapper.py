# SPDX-License-Identifier: GPL-3.0-or-later
"""Run umu, skipping its update request when the runtime is installed."""

import importlib
import os
import runpy
import sys
import zipfile
from collections.abc import Callable
from typing import cast


def main() -> None:
    target = os.environ["WLIB_UMU_RUN"]
    if zipfile.is_zipfile(target):
        sys.path.insert(0, target)
    if os.environ.get("UMU_RUNTIME_UPDATE") == "0":
        try:
            umu_run = importlib.import_module("umu.umu_run")
            umu_util = importlib.import_module("umu.umu_util")
            original = cast(Callable[..., object], getattr(umu_run, "setup_umu"))
            installed = cast(
                Callable[[object], bool], getattr(umu_util, "has_runtime_installed")
            )

            def setup(local: object, *args: object, **kwargs: object) -> object:
                try:
                    if installed(local):
                        print("[wLib] umu runtime update check skipped", flush=True)
                        return None
                except Exception:
                    pass
                print("[wLib] umu runtime update check enabled", flush=True)
                return original(local, *args, **kwargs)

            setattr(umu_run, "setup_umu", setup)
        except Exception:
            print("[wLib] umu runtime patch unavailable", flush=True)
    sys.argv[0] = target
    _ = runpy.run_path(target, run_name="__main__")


if __name__ == "__main__":
    main()
