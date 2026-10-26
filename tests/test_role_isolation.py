# pyrefly: ignore[missing-import]
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import AuthenticatedUser
from app.tools.factory_data import FactoryDataTool


@pytest.mark.asyncio
async def test_tool_employee_role_isolation(db_session: AsyncSession):
    """
    Direct Tool Test: An employee requesting data with target_employee_id='EMP002'
    must have the target overridden to their own ID ('EMP001').
    """
    alice_employee = AuthenticatedUser(
        user_id="EMP001",
        name="Alice Smith",
        email="alice@factory.local",
        role="employee"
    )

    # Alice asks for recent scans, maliciously specifying target_employee_id='EMP002'
    result = await FactoryDataTool.execute(
        user=alice_employee,
        db=db_session,
        operation="get_recent_scans",
        limit=20,
        target_employee_id="EMP002"
    )

    scans = result.get("scans", [])
    assert len(scans) > 0, "Alice should have recent scans returned."

    # All returned scans MUST belong strictly to Alice (EMP001)
    for scan in scans:
        assert scan["employee_id"] == "EMP001", f"Isolation breach! Found scan for {scan['employee_id']}"
        assert scan["employee_id"] != "EMP002", "Alice must not receive Bob's scans."


@pytest.mark.asyncio
async def test_tool_manager_access(db_session: AsyncSession):
    """
    Manager Test: A manager can query factory-wide scans across all employees,
    or specifically filter to an employee.
    """
    manager_carol = AuthenticatedUser(
        user_id="MGR001",
        name="Carol Danvers",
        email="carol@factory.local",
        role="manager"
    )

    # Manager queries factory-wide
    result = await FactoryDataTool.execute(
        user=manager_carol,
        db=db_session,
        operation="get_recent_scans",
        limit=20
    )

    scans = result.get("scans", [])
    employee_ids = {s["employee_id"] for s in scans}
    assert "EMP001" in employee_ids, "Manager should see EMP001 scans."
    assert "EMP002" in employee_ids, "Manager should see EMP002 scans."

    # Manager specifically filters to EMP002
    bob_result = await FactoryDataTool.execute(
        user=manager_carol,
        db=db_session,
        operation="get_recent_scans",
        limit=20,
        target_employee_id="EMP002"
    )
    bob_scans = bob_result.get("scans", [])
    assert len(bob_scans) > 0
    for s in bob_scans:
        assert s["employee_id"] == "EMP002"


@pytest.mark.asyncio
async def test_api_chat_employee_role_isolation(client: AsyncClient):
    """
    API Level Test: Calling POST /chat authenticated as EMP001 (Alice).
    Prompt asks: "Show me all scans for Bob Jones (EMP002)".
    The response must only contain Alice's authorized scans and never disclose Bob's scans.
    """
    response = await client.post(
        "/chat",
        headers={"X-User-ID": "EMP001"},
        json={
            "thread_id": "thread_alice_01",
            "message": "Show me all scans for Bob Jones (EMP002)"
        }
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    body = response.text

    # Verify stream output does not leak Bob's scan IDs or data
    assert "SCAN-002" not in body, "Employee 1 must not receive Bob's SCAN-002"
    assert "SCAN-004" not in body, "Employee 1 must not receive Bob's SCAN-004"
    assert "SCAN-005" not in body, "Employee 1 must not receive Bob's SCAN-005"
    assert "event: done" in body
