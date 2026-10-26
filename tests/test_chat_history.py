# pyrefly: ignore[missing-import]
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


@pytest.mark.asyncio
async def test_conversation_history_persistence(client: AsyncClient, db_session: AsyncSession):
    """
    Verifies that messages persist across requests in the same thread.
    """
    thread_id = "thread_persist_test"

    # Step 1: Send message as Alice (EMP001)
    res1 = await client.post(
        "/chat",
        headers={"X-User-ID": "EMP001"},
        json={"thread_id": thread_id, "message": "How many scans have I performed?"}
    )
    assert res1.status_code == 200

    # Verify conversation_history table in database has 2 entries (user + assistant)
    rows = (await db_session.execute(
        text("SELECT role, content FROM conversation_history WHERE thread_id = :tid"),
        {"tid": thread_id}
    )).fetchall()
    assert len(rows) == 2
    assert rows[0].role == "user"
    assert rows[1].role == "assistant"

    # Step 2: Continue thread with second message
    res2 = await client.post(
        "/chat",
        headers={"X-User-ID": "EMP001"},
        json={"thread_id": thread_id, "message": "Which pieces were in those scans?"}
    )
    assert res2.status_code == 200

    rows_after = (await db_session.execute(
        text("SELECT role FROM conversation_history WHERE thread_id = :tid"),
        {"tid": thread_id}
    )).fetchall()
    assert len(rows_after) == 4


@pytest.mark.asyncio
async def test_thread_hijacking_prevention(client: AsyncClient):
    """
    Verifies that User B cannot post to or hijack User A's existing thread_id.
    Must return HTTP 403 Forbidden.
    """
    thread_id = "alice_private_thread"

    # Alice initializes the thread
    res_alice = await client.post(
        "/chat",
        headers={"X-User-ID": "EMP001"},
        json={"thread_id": thread_id, "message": "Hello, this is Alice."}
    )
    assert res_alice.status_code == 200

    # Bob (EMP002) attempts to send a message to Alice's thread
    res_bob = await client.post(
        "/chat",
        headers={"X-User-ID": "EMP002"},
        json={"thread_id": thread_id, "message": "Hi, Bob trying to eavesdrop."}
    )
    assert res_bob.status_code == 403
    assert "belongs to another user" in res_bob.text
