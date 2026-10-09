"""Engineer A bridge for Muskan's StreetCast app.

main.py does `import api` and calls:
  list_rides() -> list[{video, label, ...}]
  run_ride(video) -> RideReport dict (events/script lines include s3 source)
  run_brief(destination, time_of_day, mode) -> briefing dict

Implementation lives in tools/streetcast/api.py so the FastAPI service and this
app share one engine. When the deploy ConfigMap is flat-only, copy
tools/streetcast/api.py + queries.json next to this file (or replace this
shim with that module).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_STREETCAST = _HERE.parent / "tools" / "streetcast"
_ENGINE_PATH = _STREETCAST / "api.py"

# Prefer a co-located engine (deploy flat folder) if someone copied it here
# under another name; otherwise load tools/streetcast/api.py.
if (_HERE / "streetcast_engine.py").is_file():
    _ENGINE_PATH = _HERE / "streetcast_engine.py"
elif not _ENGINE_PATH.is_file():
    # Last resort: this file itself was replaced by the full engine.
    raise ImportError(
        "StreetCast engine not found. Expected tools/streetcast/api.py "
        "or app/streetcast_engine.py"
    )

if _STREETCAST.is_dir() and str(_STREETCAST) not in sys.path:
    sys.path.insert(0, str(_STREETCAST))

_spec = importlib.util.spec_from_file_location("streetcast_engine", _ENGINE_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load engine from {_ENGINE_PATH}")
_engine = importlib.util.module_from_spec(_spec)
# Register before exec so weave/fastapi decorators see a real module name
sys.modules["streetcast_engine"] = _engine
_spec.loader.exec_module(_engine)

list_rides = _engine.list_rides
run_ride = _engine.run_ride
run_brief = _engine.run_brief
