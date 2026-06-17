from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from rexecop import __version__
from rexecop.errors import RExecOpError
from rexecop.operation.controller import OperationController
from rexecop.runtime_ops.worker import (
    drain_queue,
    parse_trigger_payload,
    run_worker,
    trigger_operation,
)

app = typer.Typer(
    name="rexecop",
    help="Regulated Execution Operations control-plane for profile-defined workflows.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """RExecOp operations control-plane."""


@app.command("version")
def version_cmd() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command("plan")
def plan_cmd(
    profile: str = typer.Option(
        ..., "--profile", help="Registered profile name (e.g. tecrax) or path to profile.yaml."
    ),
    env: Path = typer.Option(..., "--env", help="Path to environment YAML file."),
    intent: str = typer.Option(..., "--intent", help="Profile intent id."),
    target: str = typer.Option(..., "--target", help="Target id from environment."),
    mode: str = typer.Option("dry_run", "--mode", help="Operation mode."),
) -> None:
    """Create an operation plan without executing connectors."""
    try:
        controller = OperationController()
        operation = controller.plan(
            profile_path=profile,
            environment_path=env,
            intent=intent,
            target=target,
            mode=mode,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(operation.id)


@app.command("status")
def status_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
) -> None:
    """Show current operation state."""
    try:
        item = OperationController().get_operation(operation)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        json.dumps(
            {
                "operation_id": item.id,
                "state": item.state,
                "profile": item.profile,
                "environment": item.environment,
                "intent": item.intent,
                "target": item.target,
                "mode": item.mode,
                "updated_at": item.updated_at,
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command("approve")
def approve_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
    approved_by: str = typer.Option("operator", "--by", help="Approver label."),
) -> None:
    """Approve an operation waiting for manual approval."""
    try:
        item = OperationController().approve(operation, approved_by=approved_by)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps({"operation_id": item.id, "state": item.state}, indent=2, sort_keys=True))


@app.command("pause")
def pause_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
) -> None:
    """Pause a running operation at a pause_safe step."""
    try:
        item = OperationController().pause(operation)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps({"operation_id": item.id, "state": item.state}, indent=2, sort_keys=True))


@app.command("resume")
def resume_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
) -> None:
    """Resume a paused operation."""
    try:
        item = OperationController().resume(operation)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        json.dumps(
            {"operation_id": item.id, "state": item.state, "current_step_id": item.current_step_id},
            indent=2,
            sort_keys=True,
        )
    )


@app.command("cancel")
def cancel_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
) -> None:
    """Cancel an operation before completion."""
    try:
        item = OperationController().cancel(operation)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps({"operation_id": item.id, "state": item.state}, indent=2, sort_keys=True))


@app.command("retry")
def retry_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
) -> None:
    """Retry a failed operation when the profile retry policy allows it."""
    try:
        item = OperationController().retry(operation)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        json.dumps(
            {"operation_id": item.id, "state": item.state, "current_step_id": item.current_step_id},
            indent=2,
            sort_keys=True,
        )
    )


@app.command("rollback")
def rollback_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
) -> None:
    """Execute explicit workflow rollback steps for a failed operation."""
    try:
        result = OperationController().rollback(operation)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command("queue")
def queue_cmd(
    drain: bool = typer.Option(False, "--drain", help="Start all admitted queued operations once."),
) -> None:
    """Show pending run-now queue entries, or drain the queue once."""
    controller = OperationController()
    if drain:
        try:
            started = drain_queue(controller)
        except RExecOpError as exc:
            typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(json.dumps({"started": started}, indent=2, sort_keys=True))
        return
    pending = controller.runtime.queue.list_pending()
    typer.echo(json.dumps({"pending": pending}, indent=2, sort_keys=True))


worker_app = typer.Typer(help="Background worker commands.")
app.add_typer(worker_app, name="worker")


@worker_app.command("run")
def worker_run_cmd(
    once: bool = typer.Option(False, "--once", help="Process queue once and exit."),
    poll_interval: float = typer.Option(5.0, "--poll-interval", help="Seconds between polls."),
    max_iterations: int | None = typer.Option(
        None, "--max-iterations", help="Stop after N poll iterations."
    ),
    watch_inbox: bool = typer.Option(
        False, "--watch-inbox", help="Also process .rexecop/inbox/*.json trigger files."
    ),
) -> None:
    """Poll the run-now queue and start admitted operations (systemd-friendly)."""
    controller = OperationController()
    try:
        started = run_worker(
            controller,
            once=once,
            poll_interval=poll_interval,
            max_iterations=max_iterations,
            watch_inbox=watch_inbox,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps({"started": started}, indent=2, sort_keys=True))


@app.command("trigger")
def trigger_cmd(
    env: Path | None = typer.Option(None, "--env", help="Environment YAML (overrides stdin)."),
    profile: str | None = typer.Option(None, "--profile"),
    intent: str | None = typer.Option(None, "--intent"),
    target: str | None = typer.Option(None, "--target"),
    mode: str = typer.Option("dry_run", "--mode"),
    auto_start: bool = typer.Option(False, "--auto-start"),
) -> None:
    """Create an operation from JSON stdin or CLI flags (webhook-friendly)."""
    if sys.stdin.isatty() and not all([profile, env, intent, target]):
        typer.secho(
            "error: provide JSON on stdin or --profile --env --intent --target",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    if not sys.stdin.isatty():
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            typer.secho("error: trigger stdin must be a JSON object", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        parsed = parse_trigger_payload(payload)
    else:
        parsed = {
            "profile": profile or "",
            "environment_path": env,
            "intent": intent or "",
            "target": target or "",
            "mode": mode,
            "auto_start": auto_start,
            "source": "cli",
        }

    controller = OperationController()
    try:
        operation = trigger_operation(
            controller,
            profile=str(parsed["profile"]),
            environment_path=Path(parsed["environment_path"]),
            intent=str(parsed["intent"]),
            target=str(parsed["target"]),
            mode=str(parsed.get("mode") or "dry_run"),
            source=str(parsed.get("source") or "cli"),
            auto_start=bool(parsed.get("auto_start", auto_start)),
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        json.dumps(
            {"operation_id": operation.id, "state": operation.state},
            indent=2,
            sort_keys=True,
        )
    )


@app.command("start")
def start_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
) -> None:
    """Start an approved operation (read-only auto-approves; apply requires approval)."""
    try:
        item = OperationController().start(operation)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        json.dumps(
            {"operation_id": item.id, "state": item.state, "current_step_id": item.current_step_id},
            indent=2,
            sort_keys=True,
        )
    )


@app.command("validate")
def validate_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
) -> None:
    """Re-run deterministic validation for an operation."""
    try:
        result = OperationController().validate(operation)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command("escalate")
def escalate_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
) -> None:
    """Build and persist an escalation package for a failed/blocked operation."""
    try:
        package = OperationController().escalate(operation)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(package, indent=2, sort_keys=True))


@app.command("history")
def history_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
) -> None:
    """Show operation transition and evidence history."""
    try:
        history = OperationController().get_history(operation)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(history, indent=2, sort_keys=True))


if __name__ == "__main__":
    app()
