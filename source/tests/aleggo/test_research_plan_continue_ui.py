"""Exercise the existing save, approve and start API sequence against an isolated store."""
import asyncio
from unittest.mock import AsyncMock, patch

from aleggo.api.routers import research
from aleggo.study.research_literature_intelligence import LiteratureIntelligence
from aleggo.study.research_store import ResearchStore
from aleggo.study.store import AleggoStore


def test_edited_plan_is_saved_approved_and_started_in_order(tmp_path, monkeypatch):
    monkeypatch.setenv("ALEGGO_RESEARCH_HOME", str(tmp_path))
    monkeypatch.setenv("ALEGGO_HOME", str(tmp_path))
    store = ResearchStore(AleggoStore(tmp_path / "projects"))
    project = store.create_project("测试研究", "research.meridian")
    plan = research.ResearchPlan.model_validate({"objective": "旧目标", "steps": [{"id": "s1", "title": "检查", "requiredTools": ["python"]}]})
    store.save_plan(project["id"], plan.model_dump(by_alias=True), changed_by="test", change_reason="test", change_summary="test")
    store.approve(project["id"])
    store.set_status(project["id"], "PAUSED")
    edited = plan.model_copy(update={"objective": "新目标"})
    payload = research.PlanUpdate(plan=edited, reason="研究者修改计划", saveDraft=False)

    async def fake_start(project_id, active_store, **kwargs):
        active_store.set_status(project_id, "RUNNING")

    async def execute():
        with patch.object(research, "_store", return_value=store), patch.object(
            LiteratureIntelligence, "search", new_callable=AsyncMock,
            return_value={"items": [], "availabilityStatus": "not_available", "providerStatuses": {}},
        ), patch.object(research, "start_project", side_effect=fake_start):
            saved = await research.update_research_plan(project["id"], payload)
            assert saved["status"] == "AWAITING_APPROVAL" and saved["currentVersion"] == 2
            assert saved["approvedVersion"] is None and saved["plan"]["objective"] == "新目标"
            approved = await research.approve_research_plan(project["id"])
            assert approved["status"] == "APPROVED" and approved["approvedVersion"] == 2
            started = await research.start_research(project["id"])
            assert started["status"] == "RUNNING" and started["plan"]["objective"] == "新目标"
    asyncio.run(execute())
