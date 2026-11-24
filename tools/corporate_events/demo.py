"""Run synthetic examples offline; no SEC contact or private dataset is needed."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    def offline(event: str, args: tuple[Any, ...]) -> None:
        del args
        if event.startswith(("socket.", "http.client.")):
            raise RuntimeError("SYNTHETIC_DEMO_NETWORK_DENIED")

    sys.addaudithook(offline)
    spec = importlib.util.spec_from_file_location(
        "synthetic_demo_parser", ROOT / "tools/corporate_events/event_parsers_v4.py"
    )
    assert spec and spec.loader
    parser: Any = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = parser
    spec.loader.exec_module(parser)
    examples = [
        (
            "repurchase_authorization",
            "<p>On March 5, 2014, our Board authorized a new $100 million program "
            "to repurchase common stock in the open market.</p>",
        ),
        (
            "dividend_without_comparator",
            "<p>On March 5, 2014, our Board declared a quarterly cash dividend "
            "of $0.25 per share of common stock, payable on April 1, 2014.</p>",
        ),
        (
            "intended_separation",
            "<p>The Company entered a separation agreement and intends to spin off "
            "its services business as a separate publicly traded company.</p>",
        ),
    ]
    results = []
    for name, html in examples:
        raw = html.encode()
        doc = parser.base.Document(
            "0000000123-14-000001", name + ".htm", "8-K", "BODY", "", 0, len(raw), raw
        )
        result = parser.narrative(doc, "8-K", "123")
        results.append(
            {
                "example": name,
                "synthetic_input": html,
                "observations": [
                    {
                        "family": row["family"],
                        "proposed_state": row["proposed_state"],
                        "facts": row["facts"],
                        "certified_qualifying": row["certified_qualifying"],
                        "public_time_status": row["public_time_status"],
                    }
                    for row in result["observations"]
                ],
            }
        )
    print(
        json.dumps(
            {
                "kind": "SYNTHETIC_DEMO_NOT_RESEARCH_RESULTS",
                "parser_version": parser.VERSION,
                "network_requests": 0,
                "uses_private_data": False,
                "examples": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

