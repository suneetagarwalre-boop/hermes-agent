from __future__ import annotations

from agent.human_gate_guard import HUMAN_GATE_ITERATION_CAP, HumanGateGuard


OAUTH_WALL = {
    "name": "browser_exec",
    "content": (
        "Login wall: OAuth authorization requires Suneet. "
        "Open https://accounts.example.com/oauth/authorize?client_id=hermes "
        "and approve access before this task can continue."
    ),
}


def test_human_gate_stops_at_ten_with_exact_three_part_output():
    guard = HumanGateGuard()

    for attempt in range(1, HUMAN_GATE_ITERATION_CAP):
        assert guard.observe([OAUTH_WALL], blocked_work="the calendar comparison") is None
        assert guard.iterations == attempt

    result = guard.observe([OAUTH_WALL], blocked_work="the calendar comparison")

    assert guard.iterations == 10
    assert result is not None
    lines = result.splitlines()
    assert lines == [
        "Action: Authorize the requested OAuth connection.",
        "Path: Hermes terminal → run `hermes login` and complete the displayed authorization flow.",
        "Blocked: the calendar comparison cannot continue until authorization is complete.",
    ]
    assert "accounts.example.com" not in result


def test_real_progress_resets_a_human_gate_streak():
    guard = HumanGateGuard()
    assert guard.observe([OAUTH_WALL], blocked_work="the task") is None
    assert guard.iterations == 1

    assert guard.observe(
        [{"name": "browser_exec", "content": '{"success": true, "authorized": true}'}],
        blocked_work="the task",
    ) is None
    assert guard.iterations == 0


def test_unrelated_success_in_same_batch_does_not_reset_gate():
    guard = HumanGateGuard(cap=2)
    assert guard.observe([OAUTH_WALL], blocked_work="the task") is None

    result = guard.observe(
        [
            {"name": "read_file", "content": '{"success": true}'},
            OAUTH_WALL,
        ],
        blocked_work="the task",
    )

    assert guard.iterations == 2
    assert result is not None
    assert result.startswith("Action: Authorize")


def test_permission_wall_without_url_uses_precise_interface_path():
    guard = HumanGateGuard(cap=1)
    result = guard.observe(
        [{
            "name": "computer_use",
            "content": (
                "Permission required: grant Accessibility access in macOS "
                "System Settings before continuing."
            ),
        }],
        blocked_work="desktop automation",
    )

    assert result == (
        "Action: Grant the requested permission.\n"
        "Path: macOS System Settings → Privacy & Security → Accessibility.\n"
        "Blocked: desktop automation cannot continue until permission is granted."
    )
