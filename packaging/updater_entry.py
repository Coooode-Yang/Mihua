"""PyInstaller entry point. Public key is bundled, never a signing private key."""
import argparse
import sys
from pathlib import Path

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from westie_agent.update_install import run_helper
from westie_agent.updates import UpdateConfig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args()
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    run_helper(args.target, args.work, args.parent_pid, UpdateConfig.load(bundled))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(1)
