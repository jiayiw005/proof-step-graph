#!/usr/bin/env python3
# Originally from PyPantograph by Leni Aniva (Apache 2.0).
# Modified: skip lake build if pantograph-repl binary already exists.

import subprocess, shutil, os, stat
from pathlib import Path

# -- Define paths for Pantograph source and Pantograph Python interface
PATH_PANTOGRAPH = Path("./src")
PATH_PY = Path("./pantograph")

path_executable = PATH_PY / "pantograph-repl"
repl_src = "repl.exe" if os.name == "nt" else "repl"

# -- Skip build if pre-built binary already exists
if path_executable.exists():
    print(f"[build-pantograph] Pre-built binary found at {path_executable}, skipping lake build.")
else:
    # -- Build the REPL
    with subprocess.Popen(["lake", "build", "repl"], cwd=PATH_PANTOGRAPH) as p:
        p.wait()
        if p.returncode != 0:
            raise Exception(f"Error: 'lake build repl' failed with exit code {p.returncode}.")

    # -- Copy the REPL executable to the specified path
    shutil.copyfile(PATH_PANTOGRAPH / f".lake/build/bin/{repl_src}", path_executable)

    # -- Make the REPL executable executable
    os.chmod(path_executable, os.stat(path_executable).st_mode | stat.S_IEXEC)

    # -- Copy the Lean toolchain file to the specified path
    shutil.copyfile(PATH_PANTOGRAPH / "lean-toolchain", PATH_PY / "lean-toolchain")
