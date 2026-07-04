from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

from rexecop import __version__
from rexecop.action.configure import configure_action
from rexecop.action.diff import diff_action
from rexecop.action.policy_impact import preview_action_policy_impact
from rexecop.action.surface import (
    list_actions,
    preview_action,
    show_action,
    validate_actions,
)
from rexecop.action.templates import list_action_templates
from rexecop.catalog.service import CatalogService, compile_profile_operations
from rexecop.cli_contracts import cli_contract_registry
from rexecop.cli_errors import cli_error_json, cli_error_payload, validation_cli_error
from rexecop.environment.loader import load_environment
from rexecop.environment.sanitize import validate_no_inline_secrets
from rexecop.errors import RExecOpError
from rexecop.operation.audit import (
    build_support_bundle,
    show_evidence,
    show_receipt,
    summarize_chain,
)
from rexecop.operation.controller import OperationController
from rexecop.operation.diff import (
    diff_operation_plan,
    render_operation_plan_diff,
)
from rexecop.operation.explain import explain_operation
from rexecop.operation.review import render_operation_review, review_operation
from rexecop.policy.explain import explain_operation_policy
from rexecop.profile.conformance import validate_profile_conformance
from rexecop.profile.discoverability import (
    list_capabilities_manifest,
    list_connectors_manifest,
    list_profiles_manifest,
    run_profile_workflow_harness_report,
    show_connector_manifest,
    show_profile_manifest,
)
from rexecop.profile.extension_manifest import build_extension_manifest
from rexecop.profile.loader import load_profile
from rexecop.profile.resolver import resolve_profile_path
from rexecop.profile.runbook import render_runbook_show, show_profile_runbook
from rexecop.reaction.model import ReactionContext
from rexecop.reaction.service import ReactionService
from rexecop.runtime.doctor import CHECK_BLOCKER, count_secret_refs, run_runtime_doctor
from rexecop.runtime.init import initialize_runtime_root
from rexecop.runtime.root import resolve_runtime_instance, resolve_runtime_root
from rexecop.runtime_ops.backup import create_runtime_backup, restore_runtime_backup
from rexecop.runtime_ops.recovery import run_startup_recovery
from rexecop.runtime_ops.triage import (
    collect_ops_snapshot,
    collect_runtime_status,
    explain_error,
    list_dead_letter_manifest,
    list_locks_manifest,
    show_dead_letter_item,
)
from rexecop.runtime_ops.watchdog import WatchdogService
from rexecop.runtime_ops.worker import (
    drain_queue,
    parse_trigger_payload,
    run_worker,
    trigger_event,
    trigger_operation,
)
from rexecop.secrets.doctor import run_secrets_doctor
from rexecop.secrets.suggest import suggest_secret_refs
from rexecop.storage.factory import create_store, resolve_storage_backend
from rexecop.truth_path import project_truth_path

app = typer.Typer(
    name="rexecop",
    help="Regulated Execution Operations control-plane for profile-defined workflows.",
    no_args_is_help=True,
)
targets_app = typer.Typer(help="Query an operator-owned target catalog.", no_args_is_help=True)
env_app = typer.Typer(help="Validate operator environment files.", no_args_is_help=True)
profile_app = typer.Typer(help="Validate profile contracts.", no_args_is_help=True)
profiles_app = typer.Typer(
    help="Discover registered profiles and compatibility metadata.",
    no_args_is_help=True,
)
connectors_app = typer.Typer(
    help="Discover connector backends and certification metadata.",
    no_args_is_help=True,
)
capabilities_app = typer.Typer(
    help="List neutral runtime capabilities and their sources.",
    no_args_is_help=True,
)
contracts_app = typer.Typer(help="Inspect stable public contract registries.", no_args_is_help=True)
action_app = typer.Typer(
    help="Inspect profile action metadata without backend IO.",
    no_args_is_help=True,
)
action_templates_app = typer.Typer(
    help="Built-in action configuration templates (scope 1.0).",
    no_args_is_help=True,
)
action_app.add_typer(action_templates_app, name="templates")
policy_app = typer.Typer(help="Inspect GovEngine policy decisions.", no_args_is_help=True)
operation_app = typer.Typer(help="Inspect stored operation plans.", no_args_is_help=True)
receipt_app = typer.Typer(help="Inspect redacted receipt and SCLite refs.", no_args_is_help=True)
evidence_app = typer.Typer(help="Inspect bounded operation evidence.", no_args_is_help=True)
chain_app = typer.Typer(help="Summarize digest-linked operation chains.", no_args_is_help=True)
support_app = typer.Typer(help="Build redacted support diagnostics.", no_args_is_help=True)
runbook_app = typer.Typer(help="Show profile-owned runbooks.", no_args_is_help=True)
operations_app = typer.Typer(
    help="Query profile-defined operations and target applicability.",
    no_args_is_help=True,
)
runtime_app = typer.Typer(help="Runtime triage and status.", no_args_is_help=True)
dead_letter_app = typer.Typer(help="Inspect dead-letter items.", no_args_is_help=True)
locks_app = typer.Typer(help="Inspect advisory target locks.", no_args_is_help=True)
app.add_typer(targets_app, name="targets")
app.add_typer(env_app, name="env")
app.add_typer(profile_app, name="profile")
app.add_typer(profiles_app, name="profiles")
app.add_typer(connectors_app, name="connectors")
app.add_typer(capabilities_app, name="capabilities")
app.add_typer(contracts_app, name="contracts")
app.add_typer(action_app, name="action")
app.add_typer(policy_app, name="policy")
app.add_typer(operation_app, name="operation")
app.add_typer(receipt_app, name="receipt")
app.add_typer(evidence_app, name="evidence")
app.add_typer(chain_app, name="chain")
app.add_typer(support_app, name="support")
app.add_typer(runbook_app, name="runbook")
app.add_typer(operations_app, name="operations")
app.add_typer(runtime_app, name="runtime")
app.add_typer(dead_letter_app, name="dead-letter")
app.add_typer(locks_app, name="locks")
backup_app = typer.Typer(
    help="Backup and restore the operator runtime store.",
    no_args_is_help=True,
)
secrets_app = typer.Typer(
    help="Inspect secret references without resolving or printing values.",
    no_args_is_help=True,
)
app.add_typer(backup_app, name="backup")
app.add_typer(secrets_app, name="secrets")

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


def _emit_cli_error(payload: dict[str, object]) -> None:
    typer.echo(cli_error_json(payload))
    raise typer.Exit(code=1)


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


@secrets_app.command("doctor")
def secrets_doctor_cmd(
    env: Path | None = typer.Option(None, "--env", help="Environment YAML to inspect."),
    catalog: Path | None = typer.Option(None, "--catalog", help="Optional catalog YAML."),
    secrets_file: Path | None = typer.Option(
        None,
        "--secrets-file",
        help="Optional secrets YAML path; defaults to REXECOP_SECRETS_FILE.",
    ),
) -> None:
    """Check secret refs, duplicates, secrets-file policy and redaction self-test."""
    if env is None and catalog is None:
        typer.secho(
            "error: provide --env and/or --catalog",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        result = run_secrets_doctor(
            env_path=env,
            catalog_path=catalog,
            secrets_file=secrets_file,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] == CHECK_BLOCKER:
        raise typer.Exit(code=1)


@secrets_app.command("suggest-ref")
def secrets_suggest_ref_cmd(
    env: Path = typer.Option(..., "--env", help="Environment YAML to inspect."),
    connector: str | None = typer.Option(None, "--connector", help="Optional connector name."),
) -> None:
    """Suggest secret reference names without reading secret stores."""
    try:
        result = suggest_secret_refs(env_path=env, connector=connector)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


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


@profiles_app.command("list")
def profiles_list_cmd() -> None:
    """List registered profiles with compatibility summaries."""
    typer.echo(json.dumps(list_profiles_manifest(), indent=2, sort_keys=True))


@profiles_app.command("show")
def profiles_show_cmd(
    profile: str = typer.Argument(..., help="Registered profile name or profile path."),
    track: str = typer.Option(
        "readonly",
        "--track",
        help="Developer-check conformance track: readonly, mutation or all.",
    ),
) -> None:
    """Show one profile with intents, tracks and developer-check metadata."""
    try:
        result = show_profile_manifest(profile, track=track)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
    if result["compatibility"]["readonly"] != "passed":
        raise typer.Exit(code=1)


@profile_app.command("manifest")
def profile_manifest_cmd() -> None:
    """Emit the stack extension manifest for profiles, plugins and resolvers."""
    typer.echo(json.dumps(build_extension_manifest(), indent=2, sort_keys=True))


@connectors_app.command("list")
def connectors_list_cmd() -> None:
    """List built-in and plugin connector backends."""
    typer.echo(json.dumps(list_connectors_manifest(), indent=2, sort_keys=True))


@connectors_app.command("show")
def connectors_show_cmd(
    backend: str = typer.Argument(..., help="Connector backend class or plugin name."),
) -> None:
    """Show one connector backend descriptor and plugin compatibility status."""
    try:
        result = show_connector_manifest(backend)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@capabilities_app.command("list")
def capabilities_list_cmd() -> None:
    """List neutral capabilities known to the runtime and their source."""
    typer.echo(json.dumps(list_capabilities_manifest(), indent=2, sort_keys=True))


@contracts_app.command("cli")
def contracts_cli_cmd() -> None:
    """Emit machine-readable CLI schema and exit-code contracts."""
    typer.echo(json.dumps(cli_contract_registry(), indent=2, sort_keys=True))


@action_app.command("list")
def action_list_cmd(
    profile: str | None = typer.Option(None, "--profile", help="Registered profile or path."),
    env: Path | None = typer.Option(None, "--env", help="Environment YAML for backend bindings."),
    catalog: Path | None = typer.Option(None, "--catalog", help="Private target catalog YAML."),
    target: str | None = typer.Option(None, "--target", help="Catalog target id."),
) -> None:
    """List profile actions and redacted action metadata without backend IO."""
    try:
        result = list_actions(profile=profile, env=env, catalog=catalog, target=target)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@action_app.command("show")
def action_show_cmd(
    intent: str = typer.Argument(..., help="Profile intent/action id."),
    profile: str | None = typer.Option(None, "--profile", help="Registered profile or path."),
    env: Path | None = typer.Option(None, "--env", help="Environment YAML for backend bindings."),
    catalog: Path | None = typer.Option(None, "--catalog", help="Private target catalog YAML."),
    target: str | None = typer.Option(None, "--target", help="Catalog target id."),
) -> None:
    """Show one action contract, refs and constraints without backend IO."""
    try:
        result = show_action(intent, profile=profile, env=env, catalog=catalog, target=target)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@action_app.command("validate")
def action_validate_cmd(
    profile: str | None = typer.Option(None, "--profile", help="Registered profile or path."),
    env: Path | None = typer.Option(None, "--env", help="Environment YAML for backend bindings."),
    catalog: Path | None = typer.Option(None, "--catalog", help="Private target catalog YAML."),
    target: str | None = typer.Option(None, "--target", help="Catalog target id."),
    intent: str | None = typer.Option(None, "--intent", help="Optional single action id."),
    all_actions: bool = typer.Option(False, "--all", help="Validate all profile actions."),
) -> None:
    """Validate profile/env action bindings without backend IO."""
    if not all_actions and intent is None:
        typer.secho(
            "error: action validate requires --all or --intent",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        result = validate_actions(
            profile=profile,
            env=env,
            catalog=catalog,
            target=target,
            intent=intent,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@action_app.command("preview")
def action_preview_cmd(
    intent: str = typer.Argument(..., help="Profile intent/action id."),
    profile: str | None = typer.Option(None, "--profile", help="Registered profile or path."),
    env: Path | None = typer.Option(None, "--env", help="Environment YAML for backend bindings."),
    catalog: Path | None = typer.Option(None, "--catalog", help="Private target catalog YAML."),
    target: str | None = typer.Option(None, "--target", help="Catalog target id."),
) -> None:
    """Preview redacted effective call shapes without backend IO."""
    try:
        result = preview_action(intent, profile=profile, env=env, catalog=catalog, target=target)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@action_app.command("policy-preview")
def action_policy_preview_cmd(
    intent: str = typer.Argument(..., help="Profile intent/action id."),
    profile: str | None = typer.Option(None, "--profile", help="Registered profile or path."),
    env: Path | None = typer.Option(None, "--env", help="Environment YAML for backend bindings."),
    catalog: Path | None = typer.Option(None, "--catalog", help="Private target catalog YAML."),
    target: str = typer.Option(..., "--target", help="Target id from environment/catalog."),
    mode: str = typer.Option("dry_run", "--mode", help="Operation mode for policy simulation."),
) -> None:
    """Simulate GovEngine policy impact for one action without admission authority."""
    try:
        result = preview_action_policy_impact(
            intent,
            profile=profile,
            env=env,
            catalog=catalog,
            target=target,
            mode=mode,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] == "blocked":
        raise typer.Exit(code=1)


@action_templates_app.command("list")
def action_templates_list_cmd() -> None:
    """List built-in readonly action configuration templates."""
    typer.echo(json.dumps(list_action_templates(), indent=2, sort_keys=True))


@action_app.command("configure")
def action_configure_cmd(
    intent: str = typer.Argument(..., help="Profile intent/action id."),
    profile: str | None = typer.Option(None, "--profile", help="Registered profile or path."),
    env: Path = typer.Option(..., "--env", help="Environment YAML to inspect."),
    catalog: Path | None = typer.Option(None, "--catalog", help="Private target catalog YAML."),
    target: str | None = typer.Option(None, "--target", help="Catalog target id."),
    template: str | None = typer.Option(
        None,
        "--template",
        help="Optional built-in template id (e.g. http.simple-get).",
    ),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Always dry-run in M5."),
    write_patch: Path | None = typer.Option(
        None,
        "--write-patch",
        help="Write bounded patch operations to this file; never modifies --env.",
    ),
) -> None:
    """Generate a bounded env patch for one action without mutating env YAML."""
    if not dry_run:
        typer.secho(
            "error: action configure only supports --dry-run in M5",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        result = configure_action(
            intent,
            profile=profile,
            env=env,
            catalog=catalog,
            target=target,
            write_patch=write_patch,
            template_id=template,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@action_app.command("diff")
def action_diff_cmd(
    intent: str = typer.Argument(..., help="Profile intent/action id."),
    profile: str | None = typer.Option(None, "--profile", help="Registered profile or path."),
    env: Path = typer.Option(..., "--env", help="Environment YAML to compare."),
    catalog: Path | None = typer.Option(None, "--catalog", help="Private target catalog YAML."),
    target: str | None = typer.Option(None, "--target", help="Catalog target id."),
) -> None:
    """Compare profile connector contracts against environment bindings without backend IO."""
    try:
        result = diff_action(
            intent,
            profile=profile,
            env=env,
            catalog=catalog,
            target=target,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] == "drifted":
        raise typer.Exit(code=1)


@profile_app.command("harness")
def profile_harness_cmd(
    profile: str = typer.Option(..., "--profile", help="Registered profile or profile path."),
    env: Path | None = typer.Option(
        None,
        "--env",
        help="Optional fixture environment override for harness execution.",
    ),
) -> None:
    """Run the profile workflow test harness against a fixture environment."""
    from rexecop.profile.workflow_harness import HarnessFixture, resolve_harness_fixture

    fixture = resolve_harness_fixture(profile)
    if env is not None:
        if fixture is None:
            from rexecop.profile.resolver import resolve_profile_path

            fixture = HarnessFixture(
                profile_path=resolve_profile_path(profile),
                environment_path=env.expanduser().resolve(),
                readonly_intent="inspect_fixture_state",
                blocked_intent="apply_fixture_change",
                target="fixture-target",
            )
        else:
            fixture = HarnessFixture(
                profile_path=fixture.profile_path,
                environment_path=env.expanduser().resolve(),
                readonly_intent=fixture.readonly_intent,
                blocked_intent=fixture.blocked_intent,
                target=fixture.target,
                blocked_mode=fixture.blocked_mode,
            )
    try:
        result = run_profile_workflow_harness_report(profile, fixture=fixture)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] == "failed":
        raise typer.Exit(code=1)


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
        _emit_cli_error(
            validation_cli_error(
                command=("profile", "lint"),
                reason_code="profile_conformance_unavailable",
                message=str(exc),
                safe_next_actions=("Check the profile path or registered profile name.",),
            )
        )
    if result.status != "passed":
        _emit_cli_error(
            cli_error_payload(
                error_class="validation_error",
                reason_code="profile_conformance_failed",
                message=f"profile conformance failed for {result.profile}",
                command=("profile", "lint"),
                safe_next_actions=("Fix reported profile conformance errors.",),
                details=result.as_dict(),
            )
        )
    typer.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))


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
        _emit_cli_error(
            validation_cli_error(
                command=("operation", "explain"),
                reason_code="operation_lookup_failed",
                message=str(exc),
                safe_next_actions=(
                    "Check the operation id.",
                    "Run rexecop status --operation <id> from the same runtime root.",
                ),
            )
        )
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@operation_app.command("truth-path")
def operation_truth_path_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
) -> None:
    """Project digest-bound truth-path summary for a stored operation."""
    try:
        controller = _controller()
        item = controller.get_operation(operation)
        plan = controller.store.load_plan(operation)
        result = project_truth_path(item, plan)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@operation_app.command("diff")
def operation_diff_cmd(
    operation: str = typer.Option(..., "--operation", help="Operation id."),
    fmt: str = typer.Option(
        "json",
        "--format",
        help="Output format: json, table or markdown.",
    ),
) -> None:
    """Compare stored plan bindings against current profile/env/catalog state."""
    try:
        controller = _controller()
        item = controller.get_operation(operation)
        plan = controller.store.load_plan(operation)
        result = diff_operation_plan(item, plan)
        output = render_operation_plan_diff(result, fmt)
    except (RExecOpError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(output, nl=not output.endswith("\n"))
    if result["status"] == "drifted":
        raise typer.Exit(code=1)


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


@receipt_app.command("show")
def receipt_show_cmd(
    operation: str = typer.Argument(..., help="Operation id."),
) -> None:
    """Show redacted receipt export and SCLite refs for one operation."""
    try:
        controller = _controller()
        item = controller.get_operation(operation)
        plan = controller.store.load_plan(operation)
        result = show_receipt(item, plan, controller.store)
    except RExecOpError as exc:
        _emit_cli_error(
            validation_cli_error(
                command=("receipt", "show"),
                reason_code="receipt_lookup_failed",
                message=str(exc),
                safe_next_actions=(
                    "Check the operation id.",
                    "Run rexecop history --operation <id> from the same runtime root.",
                ),
            )
        )
    if result["status"] == "broken":
        _emit_cli_error(
            cli_error_payload(
                error_class="missing_artifact",
                reason_code="receipt_broken_digest",
                message=f"receipt artifacts failed digest verification for {operation}",
                command=("receipt", "show"),
                safe_next_actions=(
                    "Inspect SCLite bundle files before trusting this runtime root.",
                    f"Run rexecop support bundle {operation} --redacted",
                ),
                details=result,
            )
        )
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@evidence_app.command("show")
def evidence_show_cmd(
    operation: str = typer.Argument(..., help="Operation id."),
) -> None:
    """Show bounded, redacted evidence events for one operation."""
    try:
        controller = _controller()
        item = controller.get_operation(operation)
        result = show_evidence(item, controller.store)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@chain_app.command("summary")
def chain_summary_cmd(
    operation_or_reaction: str = typer.Argument(..., help="Operation id or reaction id."),
) -> None:
    """Summarize digest-linked operation, reaction, evidence and SCLite refs."""
    try:
        controller = _controller()
        item = controller.get_operation(operation_or_reaction)
        plan = controller.store.load_plan(operation_or_reaction)
        result = summarize_chain(item, plan, controller.store)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@support_app.command("bundle")
def support_bundle_cmd(
    operation: str = typer.Argument(..., help="Operation id."),
    redacted: bool = typer.Option(
        False,
        "--redacted",
        help="Required: emit only redacted diagnostic content.",
    ),
) -> None:
    """Emit a redacted support bundle projection for one operation."""
    try:
        controller = _controller()
        item = controller.get_operation(operation)
        plan = controller.store.load_plan(operation)
        result = build_support_bundle(item, plan, controller.store, redacted=redacted)
    except RExecOpError as exc:
        _emit_cli_error(
            validation_cli_error(
                command=("support", "bundle"),
                reason_code="support_bundle_unavailable",
                message=str(exc),
                safe_next_actions=("Run support bundle with --redacted for diagnostic output.",),
            )
        )
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] == "action_required":
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


@operations_app.command("unavailable")
def operations_unavailable_cmd(
    target: str = typer.Option(..., "--target", help="Target id from the private catalog."),
    catalog: Path = typer.Option(..., "--catalog", help="Private target catalog YAML."),
    intent: str | None = typer.Option(
        None,
        "--intent",
        help="Optional profile intent id; defaults to all profile operations.",
    ),
) -> None:
    """List operations that are not technically applicable to one catalog target."""
    try:
        result = CatalogService(catalog).list_unavailable_operations_for_target(
            target,
            intent=intent,
        )
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
        from rexecop.profile.operator_metadata import explain_profile_operation

        loaded = load_profile(resolve_profile_path(profile))
        result = explain_profile_operation(loaded, intent)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


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
                "schema": "rexecop.operation_status.v0.1",
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


@runtime_app.command("recover")
def runtime_recover_cmd(
    as_json: bool = typer.Option(True, "--json", help="Emit JSON recovery report."),
) -> None:
    """Reconcile stale leases, interrupted operations and receipt gaps after restart."""
    if not as_json:
        typer.secho("error: only --json output is supported", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        controller = _controller()
        result = run_startup_recovery(controller.store, controller=controller)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@runtime_app.command("status")
def runtime_status_cmd(
    as_json: bool = typer.Option(True, "--json", help="Emit JSON status."),
) -> None:
    """Show runtime queue, active operations, locks and dead-letter summary."""
    if not as_json:
        typer.secho("error: only --json output is supported", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        result = collect_runtime_status(_controller().store)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@dead_letter_app.command("list")
def dead_letter_list_cmd() -> None:
    """List dead-letter inbox payloads moved by watchdog."""
    try:
        result = list_dead_letter_manifest(_controller().store)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@dead_letter_app.command("show")
def dead_letter_show_cmd(
    name: str = typer.Argument(..., help="Dead-letter file name."),
) -> None:
    """Show one redacted dead-letter payload."""
    try:
        result = show_dead_letter_item(_controller().store, name)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@locks_app.command("list")
def locks_list_cmd() -> None:
    """List advisory target locks and stale holders."""
    try:
        result = list_locks_manifest(_controller().store)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command("ops")
def ops_cmd() -> None:
    """Aggregate queue, active operations, blockers and action-required items."""
    try:
        result = collect_ops_snapshot(_controller().store)
    except RExecOpError as exc:
        _emit_cli_error(
            validation_cli_error(
                command=("ops",),
                reason_code="ops_unavailable",
                message=str(exc),
                safe_next_actions=("Run rexecop runtime status --json.",),
            )
        )
    if result.get("blockers"):
        _emit_cli_error(
            cli_error_payload(
                error_class="runtime_failure",
                reason_code="runtime_blockers_present",
                message="runtime blockers require operator action",
                command=("ops",),
                safe_next_actions=("Inspect details.blockers and action_required.",),
                details=result,
            )
        )
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command("explain-error")
def explain_error_cmd(
    ref: str = typer.Argument(..., help="Operation id, dead-letter name or watchdog record id."),
) -> None:
    """Map a runtime failure reference to a bounded failure class and next actions."""
    try:
        result = explain_error(_controller().store, ref)
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


@backup_app.command("create")
def backup_create_cmd(
    output: Path = typer.Option(..., "--output", help="Archive path or output directory."),
) -> None:
    """Create a secret-scanned tarball backup of the runtime store."""
    try:
        result = create_runtime_backup(_controller().store.root, output=output)
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@backup_app.command("restore")
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
            target_root=_controller().store.root,
            manifest=manifest,
        )
    except RExecOpError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


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
