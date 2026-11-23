import os
from pathlib import Path
import subprocess
import sys


def test_development_settings_default_debug_off():
    backend_dir = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    for key in ("DJANGO_ENV", "DJANGO_SECRET_KEY", "DJANGO_DEBUG"):
        env.pop(key, None)
    env["DJANGO_ENV"] = "development"
    result = subprocess.run(
        [sys.executable, "-c", "import config.settings; print(config.settings.DEBUG)"],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
