from __future__ import annotations

import argparse
import sys

from skillrunner.cli.commands.dev import dev
from skillrunner.cli.commands.doctor import doctor
from skillrunner.cli.commands.lint import lint
from skillrunner.cli.commands.replay import replay
from skillrunner.cli.commands.run import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skillrunner", description="SkillRunner procedure-first runtime CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("path")

    p_dev = sub.add_parser("dev")
    p_dev.add_argument("path")

    p_lint = sub.add_parser("lint")
    p_lint.add_argument("path")

    p_replay = sub.add_parser("replay")
    p_replay.add_argument("path")

    sub.add_parser("doctor")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return run(args.path)
    if args.command == "dev":
        return dev(args.path)
    if args.command == "lint":
        return lint(args.path)
    if args.command == "replay":
        return replay(args.path)
    if args.command == "doctor":
        return doctor()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
