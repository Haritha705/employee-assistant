# pyrefly: ignore[missing-import]
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_cost_summary_manager_authorization(client: AsyncClient):
    """
    Verifies that standard employees are forbidden from accessing metrics,
    and only managers are permitted.
    """
    # Employee Alice attempts access -> HTTP 403
    res_emp = await client.get("/metrics/cost-summary", headers={"X-User-ID": "EMP001"})
    assert res_emp.status_code == 403

    # Manager Carol accesses -> HTTP 200
    res_mgr = await client.get("/metrics/cost-summary", headers={"X-User-ID": "MGR001"})
    assert res_mgr.status_code == 200
    data = res_mgr.json()
    assert "total_requests" in data
    assert "successful_requests" in data
    assert "failed_requests" in data
    assert "estimated_cost_usd" in data
    assert "pricing_rates" in data
