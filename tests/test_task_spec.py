from sogi.core.task_spec import TaskSpec


def test_task_spec_extracts_stable_unique_concepts() -> None:
    spec = TaskSpec.from_prompt(
        "Fix expired refresh token redirect without changing OAuth",
        acceptance_criteria=("Redirect to /login",),
        constraints=("Preserve OAuth",),
    )

    assert spec.objective == "Fix expired refresh token redirect without changing OAuth"
    assert spec.concepts == (
        "expired",
        "refresh",
        "token",
        "redirect",
        "without",
        "changing",
        "oauth",
    )
    assert spec.acceptance_criteria == ("Redirect to /login",)
    assert spec.constraints == ("Preserve OAuth",)
