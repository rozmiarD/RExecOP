from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

from rexecop import __version__
from rexecop.catalog.service import (
    CatalogService,
    compile_operation_descriptor,
    compile_profile_operations,
)
from rexecop.environment.loader import load_environment
from rexecop.environment.sanitize import validate_no_inline_secrets
from rexecop.errors import RExecOpError
from rexecop.operation.controller import OperationController
from rexecop.operation.explain import explain_operation
from rexecop.operation.review import render_operation_review, review_operation
from rexecop.policy.explain import explain_operation_policy
from rexecop.profile.conformance import validate_profile_conformance
from rexecop.profile.loader import load_profile
from rexecop.profile.resolver import resolve_profile_path
from rexecop.profile.runbook import render_runbook_show, show_profile_runbook
from rexecop.reaction.model import ReactionContext
from rexecop.reaction.service import ReactionService
from rexecop.runtime.doctor import CHECK_BLOCKER, count_secret_refs, run_runtime_doctor
from rexecop.runtime.init import initialize_runtime_root
from rexecop.runtime.root import resolve_runtime_instance, resolve_runtime_root
from rexecop.runtime_ops.watchdog import WatchdogService
from rexecop.runtime_ops.worker import (
    drain_queue,
    parse_trigger_payload,
    run_worker,
    trigger_event,
    trigger_operation,
)
from rexecop.storage.factory import create_store, resolve_storage_backend

app = typer.Typer(
    name="rexecop",
    help="Regulated Execution Operations control-plane for profile-defined workflows.",
    no_args_is_help=True,
)
targets_app = typer.Typer(help="Query an operator-owned target catalog.", no_args_is_help=True)
env_app = typer.Typer(help="Validate operator environment files.", no_args_is_help=True)
profile_app = typer.Typer(help="Validate profile contracts.", no_args_is_help=True)
policy_app = typer.Typer(help="Inspect GovEngine policy decisions.", no_args_is_help=True)
operation_app = typer.Typer(help="Inspect stored operation plans.", no_args_is_help=True)
runbook_app = typer.Typer(help="Show profile-owned runbooks.", no_args_is_help=True)
operations_app = typer.Typer(
    help="Query profile-defined operations and target applicability.",
    no_args_is_help=True,
)
app.add_typer(targets_app, name="targets")
app.add_typer(env_app, name="env")
app.add_typer(profile_app, name="profile")
app.add_typer(policy_app, name="policy")
app.add_typer(operation_app, name="operation")
app.add_typer(runbook_app, name="runbook")
app.add_typer(operations_app, name="operations")

_runtime_root: Path | None = None
_runtime_instance: str | None = None


@app.callback()
def main(
    root: Path | None = typer.Option(
        None,
        "--root",
        envvar="REXECOP_ROOT",
        help="Runtime root directory. Defaults to ./.rexecop.",
    ),
    instance: str | None = typer.Option(
        None,
        "--instance",
        envvar="REXECOP_INSTANCE",
        help="Named runtime instance under ./.rexecop/instances when --root is omitted.",
    ),
    storage: str = typer.Option(
        "file",
        "--storage",
        envvar="REXECOP_STORAGE",
        help="Runtime storage backend: file (default) or sqlite.",
    ),
) -> None:
    """RExecOp operations control-plane."""
    global _runtime_instance, _runtime_root
    _runtime_instance = resolve_runtime_instance(instance)
    _runtime_root = resolve_runtime_root(root, instance=_runtime_instance)
    os.environ["REXECOP_STORAGE"] = resolve_storage_backend(storage)


def _controller() -> OperationController:
    return OperationController(store=create_store(_runtime_root))


def _reaction_service() -> ReactionService:
    return ReactionService(_controller())


@app.command("version")
def version_cmd() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command("init")
def init_cmd(
    guided: bool = typer.Option(
        False,
        "--guided",
        help="Include first-run next steps without creating secrets or doing backend IO.",
    ),
) -> None:
    """Create the runtime root layout without secrets or backend IO."""
    try:
        result = initialize_runtime_root(
            _runtime_root or resolve_runtime_root(),
            backend=os.environ.get("REXECOP_STORAGE"),
            instance=_runtime_instance,
            guided=guided,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command("doctor")
def doctor_cmd(
    profile: str | None = typer.Option(None, "--profile", help="Registered profile or path."),
    env: Path | None = typer.Option(None, "--env", help="Optional environment YAML."),
    catalog: Path | None = typer.Option(None, "--catalog", help="Optional target catalog YAML."),
) -> None:
    """Check runtime root, stack compatibility and optional operator inputs."""
    result = run_runtime_doctor(
        _runtime_root or resolve_runtime_root(),
        storage_backend=os.environ.get("REXECOP_STORAGE"),
        instance=_runtime_instance,
        profile=profile,
        env_path=env,
        catalog_path=catalog,
    )
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] == CHECK_BLOCKER:
        raise typer.Exit(code=1)


@env_app.command("lint")
def env_lint_cmd(
    env: Path = typer.Option(..., "--env", help="Environment YAML to validate."),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Optional registered profile or profile path expected by the environment.",
    ),
) -> None:
    """Validate an environment file and verify it contains no inline secrets."""
    try:
        environment = load_environment(env)
        validate_no_inline_secrets(environment.as_dict())
        expected_profile = ""
        if profile:
            expected_profile = load_profile(resolve_profile_path(profile)).name
        if expected_profile and environment.profile and environment.profile != expected_profile:
            raise RExecOpError(
                f"environment profile mismatch: {environment.profile} != {expected_profile}"
            )
        result = {
            "status": "passed",
            "environment": {
                "id": environment.id,
                "profile": environment.profile,
                "target_count": len(environment.targets),
                "connector_count": len(environment.connectors),
                "secret_ref_count": count_secret_refs(environment.as_dict()),
            },
        }
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@profile_app.command("lint")
def profile_lint_cmd(
    profile: str = typer.Option(..., "--profile", help="Registered profile or profile path."),
    track: str = typer.Option(
        "readonly",
        "--track",
        help="Conformance track: readonly, mutation or all.",
    ),
) -> None:
    """Validate profile conformance for the selected track."""
    try:
        result = validate_profile_conformance(
            profile,
            require_reaction_observation=False,
            track=track,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    if result.status != "passed":
        raise typer.Exit(code=1)


@policy_app.command("explain")
def policy_explain_cmd(
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Registered profile or profile path; optional when --catalog is supplied.",
    ),
    env: Path | None = typer.Option(
        None,
        "--env",
        help="Environment YAML; optional when --catalog is supplied.",
    ),
    catalog: Path | None = typer.Option(
        None,
        "--catalog",
        help="Private target catalog; supplies profile/environment binding.",
    ),
    intent: str = typer.Option(..., "--intent", help="Profile intent id."),
    target: str = typer.Option(..., "--target", help="Target id from environment/catalog."),
    mode: str = typer.Option("dry_run", "--mode", help="Operation mode."),
) -> None:
    """Explain the GovEngine policy path for one operation-shaped request."""
    try:
        result = explain_operation_policy(
            profile_path=profile,
            environment_path=env,
            intent=intent,
            target=target,
            mode=mode,
            catalog_path=catalog,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] == "blocked":
        raise typer.Exit(code=1)


@operation_app.command("explain")
def operation_explain_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
) -> None:
    """Explain a stored operation plan without executing or approving it."""
    try:
        controller = _controller()
        item = controller.get_operation(operation)
        plan = controller.store.load_plan(operation)
        result = explain_operation(item, plan)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@operation_app.command("review")
def operation_review_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
    fmt: str = typer.Option(
        "json",
        "--format",
        help="Output format: json, table or markdown.",
    ),
) -> None:
    """Render a decision screen for a stored plan without executing it."""
    try:
        controller = _controller()
        item = controller.get_operation(operation)
        plan = controller.store.load_plan(operation)
        result = review_operation(item, plan)
        output = render_operation_review(result, fmt)
    except (RExecOpError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(output, nl=not output.endswith("\n"))
    if result["status"] == "blocked":
        raise typer.Exit(code=1)


@runbook_app.command("show")
def runbook_show_cmd(
    intent: str = typer.Argument(..., help="Profile intent id."),
    profile: str = typer.Option(..., "--profile", help="Registered profile or path."),
    fmt: str = typer.Option(
        "json",
        "--format",
        help="Output format: json, table or markdown.",
    ),
) -> None:
    """Show the profile-owned runbook bound to one intent."""
    try:
        result = show_profile_runbook(profile, intent)
        output = render_runbook_show(result, fmt)
    except (RExecOpError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(output, nl=not output.endswith("\n"))


@targets_app.command("list")
def targets_list_cmd(
    catalog: Path = typer.Option(..., "--catalog", help="Private target catalog YAML."),
) -> None:
    """List bounded target descriptors without connector paths or credentials."""
    try:
        service = CatalogService(catalog)
        result = {
            "catalog_version": service.version,
            "catalog_digest": service.digest,
            "targets": service.list_targets(),
        }
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@targets_app.command("show")
def targets_show_cmd(
    target: str = typer.Argument(..., help="Target id from the private catalog."),
    catalog: Path = typer.Option(..., "--catalog", help="Private target catalog YAML."),
) -> None:
    """Show one bounded target descriptor."""
    try:
        item = CatalogService(catalog).get_target(target)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(item.public_dict(), indent=2, sort_keys=True))


@operations_app.command("list")
def operations_list_cmd(
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Registered profile name or profile path when listing all operations.",
    ),
    catalog: Path | None = typer.Option(
        None,
        "--catalog",
        help="Private target catalog YAML when filtering by target.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        help="Target id; requires --catalog.",
    ),
) -> None:
    """List profile operations or their technical applicability to one target."""
    try:
        if target is not None:
            if catalog is None:
                raise RExecOpError("--target requires --catalog")
            result: dict[str, object] = {
                "target": target,
                "operations": CatalogService(catalog).list_operations_for_target(target),
            }
        else:
            if profile is None:
                raise RExecOpError("--profile is required when --target is omitted")
            loaded = load_profile(resolve_profile_path(profile))
            result = {
                "profile": {"id": loaded.name, "version": loaded.version},
                "operations": [item.as_dict() for item in compile_profile_operations(loaded)],
            }
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@operations_app.command("explain")
def operations_explain_cmd(
    intent: str = typer.Argument(..., help="Profile intent id."),
    profile: str = typer.Option(..., "--profile", help="Registered profile or path."),
) -> None:
    """Show the profile-owned operation descriptor; this is not admission."""
    try:
        loaded = load_profile(resolve_profile_path(profile))
        operation = compile_operation_descriptor(loaded, intent)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(operation.as_dict(), indent=2, sort_keys=True))


@app.command("reaction-plan")
def reaction_plan_cmd(
    profile: str = typer.Option(..., "--profile"),
    env: Path = typer.Option(..., "--env"),
    observation: Path | None = typer.Option(None, "--observation"),
    source_operation: str | None = typer.Option(None, "--operation"),
    target: str = typer.Option(..., "--target"),
    mode: str = typer.Option("dry_run", "--mode"),
    depth: int = typer.Option(0, "--depth", min=0),
    reaction_count: int = typer.Option(0, "--reaction-count", min=0),
    visited_rule_digest: list[str] | None = typer.Option(None, "--visited-rule-digest"),
) -> None:
    """Compile and evaluate one bounded profile-defined reaction."""
    try:
        result = _reaction_service().plan(
            profile_path=profile,
            environment_path=env,
            observation_path=observation,
            source_operation_id=source_operation,
            target=target,
            mode=mode,
            context=ReactionContext(
                depth=depth,
                reaction_count=reaction_count,
                visited_rule_digests=tuple(visited_rule_digest or ()),
            ),
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command("reaction-start")
def reaction_start_cmd(reaction: str = typer.Option(..., "--reaction")) -> None:
    """Start the already admitted child operation for a reaction."""
    try:
        result = _reaction_service().start(reaction)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command("reaction-replay")
def reaction_replay_cmd(reaction: str = typer.Option(..., "--reaction")) -> None:
    """Verify a persisted reaction chain without executing anything."""
    try:
        result = _reaction_service().replay(reaction)
    except (RExecOpError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command("reaction-proposal-validate")
def reaction_proposal_validate_cmd(
    profile: str = typer.Option(..., "--profile"),
    proposal: Path = typer.Option(..., "--proposal"),
) -> None:
    """Validate an untrusted advisory proposal; this never executes it."""
    try:
        result = _reaction_service().validate_proposal(
            profile_path=profile,
            proposal_path=proposal,
        )
    except (RExecOpError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command("plan")
def plan_cmd(
    profile: str | None = typer.Option(
        None, "--profile", help="Registered profile name or path to profile.yaml."
    ),
    env: Path | None = typer.Option(None, "--env", help="Path to environment YAML file."),
    catalog: Path | None = typer.Option(
        None,
        "--catalog",
        help="Private target catalog; supplies profile/environment binding.",
    ),
    intent: str = typer.Option(..., "--intent", help="Profile intent id."),
    target: str = typer.Option(..., "--target", help="Target id from environment."),
    mode: str = typer.Option("dry_run", "--mode", help="Operation mode."),
    auto_react: str | None = typer.Option(
        None,
        "--auto-react",
        help="Optional deterministic reaction mode after completion; currently: plan_only.",
    ),
) -> None:
    """Create an operation plan without executing connectors."""
    try:
        controller = _controller()
        operation = controller.plan(
            profile_path=profile,
            environment_path=env,
            intent=intent,
            target=target,
            mode=mode,
            catalog_path=catalog,
            auto_react=auto_react,
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
        item = _controller().get_operation(operation)
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
        item = _controller().approve(operation, approved_by=approved_by)
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
        item = _controller().pause(operation)
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
        item = _controller().resume(operation)
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
        item = _controller().cancel(operation)
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
        item = _controller().retry(operation)
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
        result = _controller().rollback(operation)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command("queue")
def queue_cmd(
    drain: bool = typer.Option(False, "--drain", help="Start all admitted queued operations once."),
) -> None:
    """Show pending run-now queue entries, or drain the queue once."""
    controller = _controller()
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
watchdog_app = typer.Typer(help="Runtime watchdog audit commands.", no_args_is_help=True)
app.add_typer(watchdog_app, name="watchdog")


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
    watchdog: bool = typer.Option(
        False, "--watchdog", help="Record neutral worker health and dead-letter stale inbox files."
    ),
    worker_id: str = typer.Option("local-worker", "--worker-id", help="Neutral worker identity."),
    stale_inbox_seconds: float = typer.Option(
        3600.0,
        "--stale-inbox-seconds",
        help=(
            "Move inbox JSON older than this many seconds to dead-letter "
            "when watchdog is enabled."
        ),
    ),
    stale_operation_seconds: float = typer.Option(
        3600.0,
        "--stale-operation-seconds",
        help="Record a block-autostart watchdog decision for active operations older than this.",
    ),
    inbox_retry_budget: int = typer.Option(
        3,
        "--inbox-retry-budget",
        help="Maximum failed inbox processing attempts before watchdog dead-lettering.",
    ),
) -> None:
    """Poll the run-now queue and start admitted operations (systemd-friendly)."""
    controller = _controller()
    try:
        started = run_worker(
            controller,
            once=once,
            poll_interval=poll_interval,
            max_iterations=max_iterations,
            watch_inbox=watch_inbox,
            watchdog=watchdog,
            worker_id=worker_id,
            stale_inbox_seconds=stale_inbox_seconds,
            stale_operation_seconds=stale_operation_seconds,
            inbox_retry_budget=inbox_retry_budget,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps({"started": started}, indent=2, sort_keys=True))


@watchdog_app.command("manual-record")
def watchdog_manual_record_cmd(
    action: str = typer.Option(
        ...,
        "--action",
        help="Manual watchdog action: renew_lease, mark_stale or escalate_operator.",
    ),
    reason: str = typer.Option(..., "--reason", help="Bounded operator reason."),
    actor_ref: str = typer.Option(..., "--actor-ref", help="Bounded operator reference."),
    scope: str = typer.Option(..., "--scope", help="Bounded recovery scope."),
    operation_id: str = typer.Option("", "--operation", help="Affected operation id."),
    event_ref: str = typer.Option("", "--event-ref", help="Affected event digest ref."),
    trigger_ref: str = typer.Option("", "--trigger-ref", help="Affected trigger digest ref."),
    inbox_item_name: str = typer.Option("", "--inbox-item", help="Affected inbox item name."),
) -> None:
    """Record a governed manual watchdog decision without executing recovery."""
    controller = _controller()
    try:
        record = WatchdogService(controller.store).record_manual_recovery_action(
            action=action,
            reason=reason,
            actor_ref=actor_ref,
            scope=scope,
            operation_id=operation_id,
            event_ref=event_ref,
            trigger_ref=trigger_ref,
            inbox_item_name=inbox_item_name,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(record, indent=2, sort_keys=True))


@app.command("trigger")
def trigger_cmd(
    env: Path | None = typer.Option(None, "--env", help="Environment YAML (overrides stdin)."),
    profile: str | None = typer.Option(None, "--profile"),
    intent: str | None = typer.Option(None, "--intent"),
    target: str | None = typer.Option(None, "--target"),
    mode: str = typer.Option("dry_run", "--mode"),
    auto_start: bool = typer.Option(False, "--auto-start"),
    auto_react: str | None = typer.Option(
        None,
        "--auto-react",
        help="Optional deterministic reaction mode after completion; currently: plan_only.",
    ),
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
            "auto_react": auto_react,
            "source": "cli",
        }

    controller = _controller()
    try:
        if parsed.get("kind") == "trigger_event":
            result = trigger_event(
                controller,
                profile=str(parsed["profile"]),
                environment_path=parsed["environment_path"],
                catalog_path=parsed["catalog_path"],
                event_payload=parsed["trigger_event"],
                source=str(parsed.get("source") or "cli"),
            )
            typer.echo(json.dumps(result, indent=2, sort_keys=True))
            return
        else:
            operation = trigger_operation(
                controller,
                profile=str(parsed["profile"]),
                environment_path=Path(parsed["environment_path"]),
                intent=str(parsed["intent"]),
                target=str(parsed["target"]),
                mode=str(parsed.get("mode") or "dry_run"),
                source=str(parsed.get("source") or "cli"),
                auto_start=bool(parsed.get("auto_start", auto_start)),
                auto_react=(
                    str(parsed["auto_react"])
                    if parsed.get("auto_react") is not None
                    else None
                ),
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
        item = _controller().start(operation)
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
        result = _controller().validate(operation)
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
        package = _controller().escalate(operation)
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
        history = _controller().get_history(operation)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(history, indent=2, sort_keys=True))


if __name__ == "__main__":
    app()
