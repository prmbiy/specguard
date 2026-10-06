from conflict_certifier.specguard.layout import artifact_path, saved_path
from conflict_certifier.llm.call_log import LLMCallLogger


def test_layout_and_legacy_reads(tmp_path):
    assert artifact_path(tmp_path, "cert.lean") == tmp_path / "cert.lean"
    assert artifact_path(tmp_path, "spec.lean") == tmp_path / "leans/spec.lean"
    assert artifact_path(tmp_path, "selected_tests.json") == tmp_path / "intermediates/inputs/selected_tests.json"
    assert artifact_path(tmp_path, "config.json") == tmp_path / "config/config.json"
    legacy = tmp_path / "spec.lean"
    legacy.write_text("legacy")
    assert saved_path(tmp_path, "spec.lean") == legacy
    new = artifact_path(tmp_path, "spec.lean")
    new.write_text("new")
    assert saved_path(tmp_path, "spec.lean") == new


def test_task_local_trajectory_path(tmp_path):
    logger = LLMCallLogger(tmp_path)
    logger.start_task("trajs")
    logger.begin_call(agent="connector", provider="mock", model="mock",
                      system="test", messages=[], parameters={})
    assert (tmp_path / "trajs/connector_log.json").exists()
