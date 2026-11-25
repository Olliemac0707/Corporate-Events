"""Only official same-origin search scripts can enter route discovery."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "bootstrap_test", ROOT / "tools/corporate_events/sec_search_bootstrap.py"
)
assert spec and spec.loader
m: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def test_only_local_search_scripts_are_selected() -> None:
    raw = (
        b'<script src="js/efts.js"></script><script src="//evil.test/efts.js"></script>'
        b'<script src="/Archives/edgar/data/1/body.js"></script>'
        b'<script src="js/efts.js"></script>'
    )
    assert m.script_urls(raw) == ["https://www.sec.gov/edgar/search/js/efts.js"]


def test_scripts_are_not_executed_or_promoted_from_text() -> None:
    assert m.script_urls(b'<script>fetch("https://evil.test")</script>') == []
    assert m.script_urls(b'<a href="js/efts.js">script</a>') == []


def test_traversal_normalizes_outside_allowed_path() -> None:
    assert m.script_urls(b'<script src="../other.js"></script>') == []
