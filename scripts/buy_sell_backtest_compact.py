from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from financial_dashboard.decision_audit.compact_backtest_reporting import compact_backtest_output


def main() -> None:
    script = Path(__file__).with_name("buy_sell_backtest.py")
    command = [sys.executable, str(script), *sys.argv[1:]]
    completed = subprocess.run(command, text=True, capture_output=True)

    if completed.stdout:
        print(compact_backtest_output(completed.stdout))
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
