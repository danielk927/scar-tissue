import pytest

from scar_experiments.prompts import read_system_v1


def test_read_system_v1_matches_agent_loading(tmp_path):
    path = tmp_path / "system_v1.md"
    path.write_text("Be careful.\nCall submit_answer.  \n\n")
    assert read_system_v1(path) == "Be careful.\nCall submit_answer.  "  # like the agent: only newlines stripped
    with pytest.raises(FileNotFoundError, match="system_v1 prompt file not found"):
        read_system_v1(tmp_path / "missing.md")
