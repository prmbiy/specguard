import json
from pathlib import Path

import pytest

from conflict_certifier.specguard.cli import configuration, main


def test_config_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SPECGUARD_MODEL", "example/model")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-secret")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.invalid/v1")
    config = configuration(None, None)
    assert config["time_budget"] == 1800
    assert config["api_key_env"] == "OPENROUTER_API_KEY"
    assert "dummy-secret" not in json.dumps(config)


@pytest.mark.parametrize("model", ["example/model", "claude-example"])
def test_generic_endpoint_without_config(monkeypatch, tmp_path, model):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SPECGUARD_MODEL", model)
    monkeypatch.setenv("LLM_API_KEY", "dummy-generic-secret")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    config = configuration(None, None)
    assert config["provider"] == "openai_compatible"
    assert config["api_key_env"] == "LLM_API_KEY"
    assert config["api_base_env"] == "LLM_BASE_URL"
    assert "dummy-generic-secret" not in json.dumps(config)


def test_explicit_backend_settings_win(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_API_KEY", "unused-generic")
    monkeypatch.setenv("LLM_BASE_URL", "https://generic.invalid/v1")
    monkeypatch.setenv("CUSTOM_KEY", "dummy-custom")
    monkeypatch.setenv("CUSTOM_URL", "https://custom.invalid/v1")
    path = tmp_path / "config.yaml"
    path.write_text("model: example/model\nprovider: openai_compatible\napi_key_env: CUSTOM_KEY\napi_base_env: CUSTOM_URL\n")
    config = configuration(str(path), None)
    assert config["api_key_env"] == "CUSTOM_KEY"
    assert config["api_base_env"] == "CUSTOM_URL"


def test_cli_output_path_and_exit(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("conflict_certifier.specguard.cli.configuration", lambda *_: {"model": "openai/example"})
    calls = []
    def run(task, root, output, config):
        calls.append((task, root, output))
        return {"verdict": "conflict", "evidence": "proof"}
    monkeypatch.setattr("conflict_certifier.specguard.cli.run_task", run)
    assert main(["accept dates"]) == 1
    task, root, output = calls[0]
    assert task == "accept dates" and root == tmp_path
    assert output.parent.parent == tmp_path / "output/specguard/openai_example"
    assert output.name == tmp_path.name
    assert capsys.readouterr().out.strip() == "conflict"


def test_missing_config_does_not_start_run(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPECGUARD_MODEL", raising=False)
    monkeypatch.setattr("conflict_certifier.specguard.cli.run_task", lambda *a: pytest.fail("started run"))
    assert main(["task"]) == 2
    assert "SPECGUARD_MODEL" in capsys.readouterr().err


def test_entrypoint_is_additive():
    import tomllib
    p = Path(__file__).parents[1] / "pyproject.toml"
    scripts = tomllib.loads(p.read_text())["project"]["scripts"]
    assert scripts["specguard"] == "conflict_certifier.specguard.cli:main"
    assert scripts["swebench-lean"] == "conflict_certifier.tracks.swebench.run:main"
    assert scripts["swebench-python"] == "conflict_certifier.tracks.swebench_py.run:main"
