from pathlib import Path

import pytest

from lean_eval_toolkit.config import load_settings
from lean_eval_toolkit.datasets import LeanProblem
from lean_eval_toolkit.test_templates import TestTemplateError, load_test_template


@pytest.fixture
def problem() -> LeanProblem:
    return LeanProblem(
        id="demo",
        dataset="manual",
        formal_statement="import Mathlib\ntheorem demo : True := by sorry\n",
        informal_statement="Prove True.",
        metadata={"custom_prompt": "metadata value"},
    )


def test_builtin_template_matches_training_user_message(problem: LeanProblem) -> None:
    template = load_test_template("lean-cot-v1")

    assert template.render(problem) == [
        {
            "role": "user",
            "content": (
                "Complete the following Lean 4 code:\n\n"
                "```lean4\n"
                "import Mathlib\n"
                "theorem demo : True := by sorry```\n\n"
                "Before producing the Lean 4 code to formally prove the given theorem, "
                "provide a detailed proof plan outlining the main proof steps and strategies.\n"
                "The plan should highlight key ideas, intermediate lemmas, and proof structures "
                "that will guide the construction of the final formal proof."
            ),
        }
    ]


def test_custom_template_maps_problem_and_metadata_fields(
    tmp_path: Path, problem: LeanProblem
) -> None:
    path = tmp_path / "custom.yaml"
    path.write_text(
        """schema_version: 1
id: custom
fields:
  statement:
    source: formal_statement
    transforms: [strip]
  note:
    source: metadata.custom_prompt
messages:
  - role: system
    content: "Use Lean."
  - role: user
    content: "${note}\\n${statement}"
output:
  primary:
    type: last_fenced_code
    languages: [lean4]
""",
        encoding="utf-8",
    )

    template = load_test_template(path)

    assert template.render(problem) == [
        {"role": "system", "content": "Use Lean."},
        {
            "role": "user",
            "content": "metadata value\nimport Mathlib\ntheorem demo : True := by sorry",
        },
    ]


def test_strict_extraction_reports_primary_strategy() -> None:
    template = load_test_template("lean-cot-v1")
    response = """### Detailed Proof

Use `trivial`.

### Complete Lean 4 Proof

```lean4
theorem demo : True := by trivial
```"""

    result = template.extract(response)

    assert result.code == "theorem demo : True := by trivial\n"
    assert result.strategy == "strict_complete_section"
    assert not result.used_fallback


@pytest.mark.parametrize(
    ("response", "strategy"),
    [
        (
            "### Complete Lean 4 Proof\n\n```Lean4\ntheorem x : True := by trivial\n```\nextra",
            "relaxed_complete_section",
        ),
        (
            "Explanation\n```lean4\ntheorem x : True := by trivial\n```",
            "final_lean4_fence",
        ),
        (
            "Explanation\n```lean\ntheorem x : True := by trivial\n```",
            "final_lean_or_unlabelled_fence",
        ),
        (
            "Explanation\n```lean4\ntheorem x : True := by trivial```",
            "final_lean4_fence",
        ),
        (
            "### Complete Lean 4 Proof\n\ntheorem x : True := by trivial",
            "raw_lean_after_complete_heading",
        ),
        ("theorem x : True := by trivial", "raw_lean"),
    ],
)
def test_extraction_falls_back_for_noncanonical_responses(
    response: str, strategy: str
) -> None:
    result = load_test_template("lean-cot-v1").extract(response)

    assert result.code == "theorem x : True := by trivial\n"
    assert result.strategy == strategy
    assert result.used_fallback


def test_extraction_rejects_unrecognisable_text() -> None:
    with pytest.raises(TestTemplateError, match="did not match"):
        load_test_template("lean-cot-v1").extract("I could not solve this problem.")


def test_v3_prompt_and_primary_extractor(problem: LeanProblem) -> None:
    template = load_test_template("lean-plan-repair-v3")
    assert template.render(problem) == [{
        "role": "user",
        "content": (
            "Complete the following Lean 4 theorem. Return a proof plan followed by the "
            "completed theorem declaration. Do not repeat imports.\n\n"
            "```lean4\nimport Mathlib\ntheorem demo : True := by sorry\n```"
        ),
    }]
    result = template.extract(
        "### Proof Plan\n\nUse trivial.\n\n### Lean Proof\n\n"
        "```lean4\ntheorem demo : True := by trivial\n```"
    )
    assert result.code == "theorem demo : True := by trivial\n"
    assert result.strategy == "strict_lean_proof"
    assert not result.used_fallback


@pytest.mark.parametrize("heading", ["### proof plan", "### Revised Proof Plan", "### Wrong Plan"])
def test_v3_malformed_plan_uses_fallback(heading: str) -> None:
    response = (
        f"{heading}\n\nUse trivial.\n\n### Lean Proof\n\n"
        "```lean4\ntheorem demo : True := by trivial\n```"
    )
    result = load_test_template("lean-plan-repair-v3").extract(response)
    assert result.used_fallback
    assert result.strategy == "relaxed_lean_proof"


def test_v3_unterminated_fence_is_rejected() -> None:
    response = (
        "### Proof Plan\n\nUse trivial.\n\n### Lean Proof\n\n"
        "```lean4\ntheorem demo : True := by trivial"
    )
    with pytest.raises(TestTemplateError, match="did not match"):
        load_test_template("lean-plan-repair-v3").extract(response)


def test_wuprover_selects_v3_template() -> None:
    config = Path(__file__).parents[1] / "src/lean_eval_toolkit/conf/wuprover.yaml"
    settings = load_settings(config_path=config, env_file=None)
    assert settings.evaluation.test_template == "lean-plan-repair-v3"
    assert settings.model.max_tokens == 8192
