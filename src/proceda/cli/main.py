"""ABOUTME: Root CLI application built with Typer.
ABOUTME: Registers subcommands (run, lint, replay, doctor).
"""

from __future__ import annotations

import typer

from proceda.cli.commands.cache import cache_app
from proceda.cli.commands.convert import convert
from proceda.cli.commands.doctor import doctor
from proceda.cli.commands.lint import lint
from proceda.cli.commands.replay import replay
from proceda.cli.commands.run import run

app = typer.Typer(
    name="proceda",
    help="Proceda: Turn SOPs into runnable agents with human oversight.",
    no_args_is_help=True,
    add_completion=False,
)

# Register commands
app.command()(run)
app.command()(lint)
app.command()(convert)
app.command()(replay)
app.command()(doctor)
app.add_typer(cache_app, name="cache")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
