import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "POND_DATA" not in os.environ:
    _data = tempfile.mkdtemp(prefix="pond-test-")
    atexit.register(shutil.rmtree, _data, ignore_errors=True)
    os.environ["POND_DATA"] = _data
