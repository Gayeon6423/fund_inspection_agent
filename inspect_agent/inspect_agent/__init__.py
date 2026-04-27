"""Compatibility package for running from inside `inspect_agent/`.

When current working directory is `inspect_agent`, importing `inspect_agent.*`
would normally fail because the parent project root is not on sys.path.
This package extends its search path to include the parent directory so that
`inspect_agent.api_server` and `inspect_agent.excel_json` resolve correctly.
"""

from pathlib import Path

_parent_dir = Path(__file__).resolve().parents[1]
if str(_parent_dir) not in __path__:
    __path__.append(str(_parent_dir))
