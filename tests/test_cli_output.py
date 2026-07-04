from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.cli_errors import CLI_ERROR_SCHEMA
from rexecop.errors import RExecOpError

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / 'examples/profiles/runtime-fixture/profile.yaml'
ENVIRONMENT = REPO_ROOT / 'examples/environments/runtime-fixture.example.yaml'
ENVIRONMENT_POLICY = REPO_ROOT / 'examples/environments/runtime-fixture.policy.example.yaml'

runner = CliRunner()


def test_global_json_flag_on_init_emits_runtime_init_schema(tmp_path) -> None:
    root = tmp_path / 'runtime'
    result = runner.invoke(app, ['--root', str(root), '--json', 'init'])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload['status'] == 'initialized'
    assert payload['root'] == str(root)


def test_global_format_table_on_doctor_renders_human_summary(tmp_path) -> None:
    root = tmp_path / 'missing-runtime'
    result = runner.invoke(app, ['--root', str(root), '--format', 'table', 'doctor'])

    assert result.exit_code == 1
    assert 'doctor status=blocker' in result.stdout
    assert 'blockers=runtime_root' in result.stdout


def test_global_format_markdown_on_init_renders_heading(tmp_path) -> None:
    root = tmp_path / 'runtime'
    result = runner.invoke(
        app,
        ['--root', str(root), '--format', 'markdown', 'init', '--guided'],
    )

    assert result.exit_code == 0
    assert '# RExecOp init' in result.stdout
    assert 'next_steps' not in result.stdout


def test_global_format_table_on_env_lint(tmp_path) -> None:
    env_path = tmp_path / 'env.yaml'
    env_path.write_text(
        (
            'environment:\n'
            '  id: demo-env\n'
            '  profile: demo\n'
            '  targets:\n'
            '    host-1:\n'
            '      type: fixture\n'
            '  connectors:\n'
            '    fixture:\n'
            '      enabled: true\n'
            '      backend: static_fixture\n'
            '      fixture_only: true\n'
            '      actions:\n'
            '        read:\n'
            '          data:\n'
            '            ok: true\n'
        ),
        encoding='utf-8',
    )

    result = runner.invoke(
        app,
        ['--format', 'table', 'env', 'lint', '--env', str(env_path)],
    )

    assert result.exit_code == 0
    assert 'env lint status=passed' in result.stdout
    assert 'environment=demo-env' in result.stdout


def test_global_json_on_policy_explain_failure_emits_cli_error(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            '--json',
            'policy',
            'explain',
            '--intent',
            'missing',
            '--target',
            'host-1',
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload['schema'] == CLI_ERROR_SCHEMA
    assert payload['command'] == 'policy explain'


def test_global_json_init_failure_emits_cli_error_envelope(tmp_path) -> None:
    root = tmp_path / 'runtime'

    with patch(
        'rexecop.cli.initialize_runtime_root',
        side_effect=RExecOpError('runtime root is not writable'),
    ):
        result = runner.invoke(app, ['--root', str(root), '--json', 'init'])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload['schema'] == CLI_ERROR_SCHEMA
    assert payload['command'] == 'init'
    assert payload['reason_code'] == 'runtime_init_failed'


def test_plan_explain_emits_plan_explain_schema(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            'plan',
            '--explain',
            '--profile',
            str(PROFILE),
            '--env',
            str(ENVIRONMENT_POLICY),
            '--intent',
            'inspect_fixture_state',
            '--target',
            'fixture-target',
            '--mode',
            'dry_run',
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload['schema'] == 'rexecop.plan_explain.v0.1'
    assert payload['status'] == 'ready'
    assert payload['operation_projection']['operation']['id'] == 'inspect_fixture_state'


def test_plan_explain_table_format_renders_summary() -> None:
    result = runner.invoke(
        app,
        [
            '--format',
            'table',
            'plan',
            '--explain',
            '--profile',
            str(PROFILE),
            '--env',
            str(ENVIRONMENT_POLICY),
            '--intent',
            'inspect_fixture_state',
            '--target',
            'fixture-target',
        ],
    )

    assert result.exit_code == 0, result.output
    assert 'plan explain status=ready' in result.stdout
    assert 'intent=inspect_fixture_state' in result.stdout


def test_history_table_format_after_plan(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    plan = runner.invoke(
        app,
        [
            'plan',
            '--profile',
            str(PROFILE),
            '--env',
            str(ENVIRONMENT),
            '--intent',
            'inspect_fixture_state',
            '--target',
            'fixture-target',
        ],
    )
    assert plan.exit_code == 0, plan.output
    operation_id = plan.stdout.strip()

    result = runner.invoke(
        app,
        ['--format', 'table', 'history', '--operation', operation_id],
    )

    assert result.exit_code == 0, result.output
    assert f'history operation_id={operation_id}' in result.stdout
    assert 'transitions=' in result.stdout


def test_global_json_on_approve_failure_emits_cli_error(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ['--json', 'approve', '--operation', 'missing-op'],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload['schema'] == CLI_ERROR_SCHEMA
    assert payload['command'] == 'approve'
    assert payload['reason_code'] == 'operation_lookup_failed'