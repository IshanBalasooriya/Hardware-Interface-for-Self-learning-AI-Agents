import json

from agent.prompts import SKILL_EXAMPLE, build_system_prompt


def test_prompt_shows_skill_example_and_rules():
    prompt = build_system_prompt()
    assert json.dumps(SKILL_EXAMPLE) in prompt
    assert "No other keys are allowed anywhere" in prompt


def test_prompt_asks_for_repeat_and_intent():
    prompt = build_system_prompt()
    assert "Use repeat for repeated frames" in prompt
    assert "Every shift_out, save_skill and reuse_skill call must include the intent argument" in prompt


def test_prompt_requires_registers_in_order_before_saving():
    prompt = build_system_prompt()
    assert "01[r0]02[r1]03[r2]04[r3]05[r4]06[r5]07[r6]08[r7]" in prompt
    assert "each exactly once" in prompt
    assert "Do not call save_skill until all 8 rows match" in prompt
