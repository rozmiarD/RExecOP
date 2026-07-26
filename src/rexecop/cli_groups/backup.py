from __future__ import annotations

import json
from pathlib import Path

import typer

from rexecop.cli_context import controller, runtime_root
from rexecop.errors import RExecOpError
from rexecop.runtime_ops.backup import create_runtime_backup, restore_runtime_backup

app = typer.Typer(
    help="Backup and restore the operator runtime store.",
    no_args_is_help=True,
)


@app.command("create")
def backup_create_cmd(
    output: Path = typer.Option(..., "--output", help="Archive path or output directory."),
) -> None:
    """Create a secret-scanned tarball backup of the runtime store."""
    try:
        result = create_runtime_backup(controller().store.root, output=output)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command("restore")
def backup_restore_cmd(
    archive: Path = typer.Option(..., "--archive", help="Backup tarball path."),
    manifest: Path | None = typer.Option(
        None, "--manifest", help="Optional manifest path when not adjacent to the archive."
    ),
) -> None:
    """Restore a runtime backup into the configured runtime root."""
    try:
        result = restore_runtime_backup(
            archive=archive,
            target_root=runtime_root(),
            manifest=manifest,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
