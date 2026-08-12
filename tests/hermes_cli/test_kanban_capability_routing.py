import json
from pathlib import Path

import pytest
import yaml

from hermes_cli import kanban_db as kb
from hermes_cli import profiles
from tools.kanban_tools import KANBAN_CREATE_SCHEMA, _handle_create
from tools.mcp_schema_cache import config_fingerprint


def _profile(root: Path, name: str, capabilities: list[dict], *, mcp=(), toolsets=()):
    profile_dir = root / name
    profile_dir.mkdir(parents=True)
    server_configs = {server: {"enabled": True} for server in mcp}
    config = {
        "kanban_capabilities": capabilities,
        "mcp_servers": server_configs,
        "platform_toolsets": {"cli": list(toolsets)},
    }
    (profile_dir / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )

    declared_tools: dict[str, set[str]] = {}
    for capability in capabilities:
        for server, tool_names in (capability.get("requires", {}).get("mcp_tools", {}) or {}).items():
            declared_tools.setdefault(str(server), set()).update(map(str, tool_names))
    if declared_tools:
        cache = {
            server: {
                "fingerprint": config_fingerprint(server_configs[server]),
                "tools": [{"name": tool} for tool in sorted(tool_names)],
                "utility_tools": [],
            }
            for server, tool_names in declared_tools.items()
        }
        cache_dir = profile_dir / "cache"
        cache_dir.mkdir()
        (cache_dir / "mcp_schema_cache.json").write_text(
            json.dumps(cache), encoding="utf-8"
        )


@pytest.fixture
def routed_board(tmp_path, monkeypatch):
    profile_root = tmp_path / "profiles"
    _profile(
        profile_root,
        "dave",
        [
            {"system": "ghl_reside", "actions": ["read"], "requires": {"mcp_tools": {"ghl": ["contacts_search"]}}},
            {"system": "ghl_reside", "actions": ["write"], "requires": {"mcp_tools": {"ghl": ["contacts_update"]}}},
        ],
        mcp=["ghl"],
    )
    _profile(
        profile_root,
        "martin",
        [
            {"system": "ghl_bshg", "actions": ["read"], "requires": {"mcp_tools": {"ghl": ["contacts_search"]}}},
            {"system": "ghl_bshg", "actions": ["write"], "requires": {"mcp_tools": {"ghl": ["contacts_update"]}}},
        ],
        mcp=["ghl"],
    )
    _profile(profile_root, "redd", [{"system": "content", "actions": ["draft", "review"]}])
    _profile(
        profile_root,
        "katt",
        [{"system": "research", "actions": ["read"], "requires": {"toolsets": ["web"]}}],
        toolsets=["web"],
    )
    _profile(
        profile_root,
        "alfred",
        [
            {"system": "calendar", "actions": ["read"], "requires": {"mcp_tools": {"google_workspace": ["list_events"]}}},
            {"system": "calendar", "actions": ["write"], "requires": {"mcp_tools": {"google_workspace": ["manage_event"]}}},
        ],
        mcp=["google_workspace"],
    )
    _profile(
        profile_root,
        "bernie",
        [{"system": "reporting", "actions": ["read", "generate"], "requires": {"mcp_tools": {"monday": ["board_insights"]}}}],
        mcp=["monday"],
    )
    _profile(
        profile_root,
        "stark",
        [
            {"system": "code", "actions": ["read", "write"], "requires": {"toolsets": ["terminal", "file"]}},
            {"system": "infra", "actions": ["read", "write"], "requires": {"toolsets": ["terminal", "file"]}},
        ],
        toolsets=["terminal", "file"],
    )
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: profile_root)
    conn = kb.connect(db_path=tmp_path / "kanban.db")
    try:
        yield conn
    finally:
        conn.close()


def _spawned_assignee(conn, *, title, assignee, system, action):
    task_id = kb.create_task(
        conn,
        title=title,
        body="sandbox routing test",
        assignee=assignee,
        required_system=system,
        required_action=action,
    )
    seen = []

    def spawn(task, workspace):
        seen.append(task.assignee)
        return 4242

    result = kb.dispatch_once(conn, spawn_fn=spawn)
    return task_id, seen, result, kb.get_task(conn, task_id)


@pytest.mark.parametrize(
    ("title", "wrong", "system", "action", "expected"),
    [
        ("Update Reside contact", "stark", "ghl_reside", "write", "dave"),
        ("Draft a post", "stark", "content", "draft", "redd"),
        ("Book a call", "redd", "calendar", "write", "alfred"),
    ],
)
def test_wrong_assignee_is_rerouted_before_worker_start(
    routed_board, title, wrong, system, action, expected
):
    task_id, seen, result, task = _spawned_assignee(
        routed_board,
        title=title,
        assignee=wrong,
        system=system,
        action=action,
    )

    assert seen == [expected]
    assert task.assignee == expected
    assert result.capability_rerouted == [(task_id, wrong, expected, system, action)]


@pytest.mark.parametrize(
    ("assignee", "system", "action"),
    [
        ("stark", "code", "write"),
        ("stark", "infra", "read"),
        ("dave", "ghl_reside", "write"),
        ("martin", "ghl_bshg", "read"),
        ("redd", "content", "draft"),
        ("katt", "research", "read"),
        ("alfred", "calendar", "write"),
        ("bernie", "reporting", "generate"),
    ],
)
def test_verified_capability_starts_selected_worker(routed_board, assignee, system, action):
    task_id, seen, result, task = _spawned_assignee(
        routed_board,
        title=f"Allowed {system} {action}",
        assignee=assignee,
        system=system,
        action=action,
    )

    assert seen == [assignee]
    assert task.assignee == assignee
    assert result.capability_rerouted == []
    assert task_id not in result.capability_blocked


def test_unknown_system_stops_before_dispatch_in_plain_english(routed_board):
    task_id, seen, result, task = _spawned_assignee(
        routed_board,
        title="Operate an unknown system",
        assignee="stark",
        system="mystery_crm",
        action="write",
    )

    assert seen == []
    assert task.status == "blocked"
    assert result.capability_blocked == [task_id]
    comments = kb.list_comments(routed_board, task_id)
    assert comments
    reason = comments[-1].body
    assert "cannot start" in reason.lower()
    assert "mystery_crm" in reason
    assert "no verified specialist" in reason.lower()


def test_ambiguous_specialists_stop_before_dispatch(routed_board):
    redd_config = profiles.get_profile_dir("redd") / "config.yaml"
    config = yaml.safe_load(redd_config.read_text(encoding="utf-8"))
    config["kanban_capabilities"].append(
        {"system": "calendar", "actions": ["write"]}
    )
    redd_config.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    task_id, seen, result, task = _spawned_assignee(
        routed_board,
        title="Book a call",
        assignee="stark",
        system="calendar",
        action="write",
    )

    assert seen == []
    assert task.status == "blocked"
    assert result.capability_blocked == [task_id]
    reason = kb.list_comments(routed_board, task_id)[-1].body
    assert "alfred" in reason
    assert "redd" in reason


def test_declared_capability_is_invalid_when_required_tool_is_disabled(
    routed_board, tmp_path
):
    dave_config = profiles.get_profile_dir("dave") / "config.yaml"
    config = yaml.safe_load(dave_config.read_text(encoding="utf-8"))
    config["mcp_servers"]["ghl"]["enabled"] = False
    dave_config.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    task_id, seen, result, task = _spawned_assignee(
        routed_board,
        title="Update Reside contact",
        assignee="dave",
        system="ghl_reside",
        action="write",
    )

    assert seen == []
    assert task.status == "blocked"
    assert result.capability_blocked == [task_id]


def test_declared_capability_is_invalid_when_tool_manifest_lacks_required_tool(
    routed_board,
):
    cache_path = profiles.get_profile_dir("dave") / "cache" / "mcp_schema_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    cache["ghl"]["tools"] = [{"name": "contacts_search"}]
    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    task_id, seen, result, task = _spawned_assignee(
        routed_board,
        title="Update Reside contact",
        assignee="dave",
        system="ghl_reside",
        action="write",
    )

    assert seen == []
    assert task.status == "blocked"
    assert result.capability_blocked == [task_id]


def test_agent_create_contract_requires_structured_capability():
    assert "required_system" in KANBAN_CREATE_SCHEMA["parameters"]["properties"]
    assert "required_action" in KANBAN_CREATE_SCHEMA["parameters"]["properties"]
    response = _handle_create(
        {
            "title": "Ambiguous work",
            "body": "Do an actionable thing",
            "assignee": "stark",
        },
    )
    assert "Actionable kanban cards require" in response
    assert "status-only questions directly" in response


@pytest.mark.parametrize(
    "system,action",
    [
        (" ", " "),
        ("code", " "),
        (" ", "write"),
        (123, "write"),
        ("code", ["write"]),
    ],
)
def test_agent_create_rejects_blank_partial_or_non_string_contract(system, action):
    response = _handle_create(
        {
            "title": "Invalid contract",
            "body": "Do an actionable thing",
            "assignee": "stark",
            "required_system": system,
            "required_action": action,
        }
    )
    assert "error" in response.lower()


def test_new_card_missing_contract_blocks_before_spawn(routed_board):
    task_id = kb.create_task(
        routed_board,
        title="Missing contract",
        body="sandbox routing test",
        assignee="stark",
        require_capability_contract=True,
    )
    seen = []
    result = kb.dispatch_once(
        routed_board,
        spawn_fn=lambda task, workspace: seen.append(task.assignee) or 4242,
    )

    assert seen == []
    assert result.capability_blocked == [task_id]
    assert kb.get_task(routed_board, task_id).status == "blocked"
    reason = kb.list_comments(routed_board, task_id)[-1].body
    assert "system and action were not recorded" in reason


def test_genuine_legacy_card_without_contract_remains_fail_open(routed_board):
    task_id = kb.create_task(
        routed_board,
        title="Legacy card",
        body="sandbox routing test",
        assignee="stark",
        require_capability_contract=False,
    )
    seen = []
    result = kb.dispatch_once(
        routed_board,
        spawn_fn=lambda task, workspace: seen.append(task.assignee) or 4242,
    )

    assert seen == ["stark"]
    assert task_id not in result.capability_blocked


def test_unassigned_contracted_card_routes_to_unique_specialist(routed_board):
    task_id, seen, result, task = _spawned_assignee(
        routed_board,
        title="Draft a post",
        assignee=None,
        system="content",
        action="draft",
    )

    assert seen == ["redd"]
    assert task.assignee == "redd"
    assert result.capability_rerouted == [
        (task_id, None, "redd", "content", "draft")
    ]


def test_nonprofile_assignee_does_not_bypass_unique_specialist(routed_board):
    task_id, seen, result, task = _spawned_assignee(
        routed_board,
        title="Draft a post",
        assignee="terminal-lane",
        system="content",
        action="draft",
    )

    assert seen == ["redd"]
    assert task.assignee == "redd"
    assert result.capability_rerouted == [
        (task_id, "terminal-lane", "redd", "content", "draft")
    ]


def test_dispatcher_process_secret_does_not_authorize_candidate_profile(
    tmp_path, monkeypatch
):
    profile_root = tmp_path / "profiles"
    profile_dir = profile_root / "candidate"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "kanban_capabilities": [
                    {
                        "system": "homeassistant",
                        "actions": ["write"],
                        "requires": {"toolsets": ["homeassistant"]},
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HASS_TOKEN", "dispatcher-only-token")
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: profile_root)

    assert profiles.get_profile_capabilities("candidate") == []


def test_invalid_profile_name_cannot_escape_profiles_root(tmp_path, monkeypatch):
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    outside = tmp_path / "outside"
    _profile(
        tmp_path,
        "outside",
        [{"system": "mystery", "actions": ["write"]}],
    )
    assert outside.is_dir()
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: profile_root)

    assert profiles.get_profile_capabilities("../outside") == []


def test_idempotent_retry_survives_capability_reroute(routed_board):
    task_id = kb.create_task(
        routed_board,
        title="Draft a post",
        body="sandbox routing test",
        assignee="stark",
        required_system="content",
        required_action="draft",
        require_capability_contract=True,
        idempotency_key="same-request",
    )
    kb.dispatch_once(
        routed_board,
        spawn_fn=lambda task, workspace: 4242,
    )
    assert kb.get_task(routed_board, task_id).assignee == "redd"

    retry_id = kb.create_task(
        routed_board,
        title="Draft a post",
        body="sandbox routing test",
        assignee="stark",
        required_system="content",
        required_action="draft",
        require_capability_contract=True,
        idempotency_key="same-request",
    )
    assert retry_id == task_id
