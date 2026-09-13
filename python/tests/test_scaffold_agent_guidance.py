"""Agent discovery is present in actual generated projects, without setup calls."""

import pytest

from djust.scaffolding.generator import generate_project


def test_generated_project_has_canonical_agent_guidance_and_preserves_existing(tmp_path):
    project = generate_project("guided_app", target_dir=str(tmp_path), auto_setup=False)
    entry = project / "AGENTS.md"
    guidance = entry.read_text()
    assert "https://github.com/djust-org/djust/blob/main/docs/ai/conventions.md" in guidance
    assert "installed djust release" in guidance
    entry.write_text("Project-specific instructions")
    with pytest.raises(ValueError, match="already exists"):
        generate_project("guided_app", target_dir=str(tmp_path), auto_setup=False)
    assert entry.read_text() == "Project-specific instructions"
