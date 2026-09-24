from maf_test_harness.agent.personas import PERSONAS, persona_instructions
from maf_test_harness.workflow import STAGES


def test_every_workflow_stage_has_a_persona():
    assert set(PERSONAS) == set(STAGES)


def test_personas_are_cross_language_and_no_manual_input():
    for stage in ("requirements", "test_planning", "test_design", "test_generation"):
        text = persona_instructions(stage).lower()
        assert "language" in text
        assert "do not ask" in text
        assert "repository" in text


def test_generator_persona_forbids_placeholders_and_self_execution():
    text = persona_instructions("test_generation").lower()
    assert "do not install dependencies and do not execute tests yourself" in text
    assert "placeholder" not in text or "never" in text
    assert "case_id" in text and "base_url" in text
    assert "never weaken" in text


def test_design_persona_requires_level_actor_target():
    text = persona_instructions("test_design")
    assert "`level`" in text and "`actor`" in text and "`target`" in text
    assert "live_app.available" in text


def test_requirements_persona_covers_actors_and_explicit_contracts_only():
    text = persona_instructions("requirements")
    assert "actors" in text and "api_contracts only for HTTP routes" in text


def test_execution_persona_is_a_host_step_not_a_model():
    text = persona_instructions("test_execution").lower()
    assert "never from a model" in text and "never as pass" in text
