from unittest.mock import patch

from aleggo.study.store import AleggoStore
from aleggo.study.research_store import ResearchStore
from aleggo.study.research_workbench import ResearchWorkbench


def test_new_project_clock_excludes_paused_time(tmp_path):
    store = ResearchStore(AleggoStore(tmp_path / "data"))
    project = store.create_project("Clock test", "research.meridian")
    times = iter(["2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00",
                  "2026-01-01T04:00:00+00:00", "2026-01-01T04:00:40+00:00"])
    with patch("aleggo.study.research_store._now", side_effect=lambda: next(times)):
        for status in ("RUNNING", "PAUSED", "RUNNING", "CANCELLED"):
            store.set_status(project["id"], status)
    budget = ResearchWorkbench(store).snapshot(project["id"])["budget"]
    assert (budget["researchElapsedSeconds"], budget["researchElapsedKind"]) == (100, "measured")


def test_historical_pairing_and_missing_end():
    def event(kind, summary, time):
        return {"eventType": kind, "summary": summary, "createdAt": "2026-01-01T" + time + "+00:00"}
    events = [event("research_started", "", "00:00:00"), event("research_review_required", "", "00:01:00"),
              event("decision_recorded", "research_resumed", "04:00:00"), event("decision_recorded", "research_stopped", "04:00:40")]
    state = {"cumulativeRunSeconds": None}
    assert ResearchWorkbench._research_elapsed(state, "CANCELLED", events) == (100, "estimated")
    assert ResearchWorkbench._research_elapsed(state, "CANCELLED", events[:-1]) == (None, "unavailable")
