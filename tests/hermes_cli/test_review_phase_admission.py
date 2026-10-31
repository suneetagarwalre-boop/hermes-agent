"""Review admission is not final acceptance: keep both gates without a circular dependency."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli import goals, kanban
from tools import kanban_tools


def task():
    return SimpleNamespace(id="t_review", title="Deliver reviewed automation", body="Build and test the inert artifact, obtain Marshal review, then deliver. No sends.", goal_mode=True)


@pytest.mark.parametrize("surface", ["tool", "cli"])
def test_review_gate_evaluates_review_readiness_not_final_completion(surface):
    seen = []
    def judge(**kwargs):
        seen.append(kwargs)
        if kwargs.get("evaluation_phase") == "review":
            return "done", "Candidate is ready for independent review", False, None, False
        return "continue", "Required Marshal review is outstanding", False, None, False
    with patch("agent.auxiliary_client.get_text_auxiliary_client", return_value=(object(), "test")), patch.object(kanban_tools, "_goal_judge_available", return_value=True), patch.object(kanban_tools, "judge_goal", side_effect=judge), patch.object(goals, "judge_goal", side_effect=judge):
        if surface == "tool":
            kanban_tools._goal_gate("kanban_request_review", task(), "t_review", "Artifact and tests ready; review pending")
            with pytest.raises(kanban_tools._Reject):
                kanban_tools._goal_gate("kanban_complete", task(), "t_review", "No independent review yet")
        else:
            assert kanban._goal_mode_handoff_rejection(task(), "Artifact and tests ready", evaluation_phase="review") == ("done", None)
            assert kanban._goal_mode_handoff_rejection(task(), "No independent review yet")[0] == "continue"
    assert seen[0]["evaluation_phase"] == "review"


@pytest.mark.parametrize("phase", ["review", "completion"])
def test_judge_uses_phase_specific_system_instructions(phase):
    seen = []
    def llm(call, system, user, timeout):
        seen.append((system, user))
        return json.dumps({"verdict": "done", "reason": "Evidence checked"})
    with patch.object(goals, "_call_goal_judge_llm", side_effect=llm):
        result = goals.judge_goal("Build + independent review + deliver", "Tested candidate awaiting reviewer", evaluation_phase=phase)
    assert result[0] == "done"
    system, _user = seen[0]
    if phase == "review":
        assert "review readiness" in system.lower()
        assert "do not require" in system.lower()
        assert "independent review" in system.lower()
        assert "not final" in system.lower()
        assert "missing" in system.lower()
    else:
        assert system == goals.JUDGE_SYSTEM_PROMPT


def test_unknown_phase_fails_closed():
    with pytest.raises(ValueError):
        goals.judge_goal("goal", "evidence", evaluation_phase="bypass")


@pytest.mark.parametrize("surface", ["tool", "cli"])
def test_review_still_rejects_unready_candidates(surface):
    verdict = ("continue", "Missing runnable artifact and evidence", False, None, False)
    with patch("agent.auxiliary_client.get_text_auxiliary_client", return_value=(object(), "test")), patch.object(kanban_tools, "_goal_judge_available", return_value=True), patch.object(kanban_tools, "judge_goal", return_value=verdict), patch.object(goals, "judge_goal", return_value=verdict):
        if surface == "tool":
            with pytest.raises(kanban_tools._Reject):
                kanban_tools._goal_gate("kanban_request_review", task(), "t_review", "Just a plan")
        else:
            assert kanban._goal_mode_handoff_rejection(task(), "Just a plan", evaluation_phase="review")[0] == "continue"


def test_real_tool_handoff_preserves_review_then_requires_a_concrete_verdict(tmp_path, monkeypatch):
    from pathlib import Path
    from hermes_cli import kanban_db as kb, kanban_db_connect as kbc
    home=tmp_path/'home'
    monkeypatch.setenv('HERMES_HOME',str(home))
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    for key in ('HERMES_KANBAN_DB','HERMES_KANBAN_BOARD','HERMES_KANBAN_HOME'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('HERMES_PROFILE','builder')
    reviewer=home/'profiles'/'reviewer'
    reviewer.mkdir(parents=True)
    (reviewer/'config.yaml').write_text('{}\n')
    monkeypatch.setattr(kb,'_fire_kanban_lifecycle_hook',lambda *a,**k:None)
    monkeypatch.setattr(kb,'_cleanup_workspace',lambda *a,**k:None)
    scope={'business_domain':'infrastructure','source_system':'fixture'}
    contract={**scope,'goal_id':'review-phase-integration','deliverable':'reviewed working artifact','requires_review':True,'reviewer_profile':'reviewer'}
    with kbc.connect_closing() as conn:
        tid=kb.create_task(conn,title='Deliver a reviewed artifact',body='Build, verify, obtain independent review and deliver.',assignee='builder',goal_mode=True,contract=contract)
        worker=kb.claim_task(conn,tid)
    monkeypatch.setenv('HERMES_KANBAN_TASK',tid)
    monkeypatch.setenv('HERMES_KANBAN_RUN_ID',str(worker.current_run_id))
    seen=[]
    def judge(**kwargs):
        seen.append(kwargs)
        return ('done','Ready for review',False,None,False) if kwargs.get('evaluation_phase')=='review' else ('continue','Independent review pending',False,None,False)
    monkeypatch.setattr(kanban_tools,'_goal_judge_available',lambda:True)
    monkeypatch.setattr(kanban_tools,'judge_goal',judge)
    handoff=json.loads(kanban_tools._handle_request_review({'summary':'Concrete candidate exercised; independent approval pending','metadata':scope,'reviewer':'reviewer'}))
    assert handoff.get('ok') is True
    with kbc.connect_closing() as conn:
        assert kb.get_task(conn,tid).status=='review'
        reviewer_run=kb.claim_review_task(conn,tid)
    monkeypatch.setenv('HERMES_PROFILE','reviewer')
    monkeypatch.setenv('HERMES_KANBAN_RUN_ID',str(reviewer_run.current_run_id))
    # Even an over-optimistic judge cannot turn an explicitly partial verdict into Done.
    monkeypatch.setattr(kanban_tools,'judge_goal',lambda **kw:('done','ok',False,None,False))
    partial=json.loads(kanban_tools._handle_complete({'summary':'Partial evidence only','metadata':{**scope,'review_outcome':'partial'}}))
    assert 'review approval evidence' in partial.get('error','')
    with kbc.connect_closing() as conn:
        assert kb.get_task(conn,tid).status=='running'
    approved=json.loads(kanban_tools._handle_complete({'summary':'Independently verified and approved the complete fixture','metadata':{**scope,'review_outcome':'approved','reviewer_checks':['Executed the candidate and checked its original acceptance criteria']}}))
    assert approved.get('ok') is True
    with kbc.connect_closing() as conn:
        assert kb.get_task(conn,tid).status=='done'
