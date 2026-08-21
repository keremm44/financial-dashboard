from __future__ import annotations

import sys
from pathlib import Path

from streamlit.web import cli as streamlit_cli


def main() -> None:
    """Launch the local Streamlit inspector through the installed entry point."""

    app_path = Path(__file__).with_name("app.py")
    sys.argv = [
        "streamlit",
        "run",
        str(app_path),
        "--server.address=0.0.0.0",
        "--server.headless=true",
        *sys.argv[1:],
    ]
    raise SystemExit(streamlit_cli.main())


if __name__ == "__main__":
    main()
