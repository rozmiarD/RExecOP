from __future__ import annotations

import typer

from rexecop import __version__

app = typer.Typer(
    name="rexecop",
    help="Governance-bound deterministic operations control-plane for profile-defined workflows.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """RExecOp operations control-plane."""


@app.command("version")
def version_cmd() -> None:
    """Print the package version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
