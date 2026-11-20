"""The core invariant: app_id + user_id, never a resource ID alone."""

import pytest

from app.repositories.conversations import ConversationRepository, MessageRepository
from app.repositories.errors import ResourceNotFound


async def test_same_user_id_in_two_apps_is_isolated(
    seeded_apps, session, agri_context, mgmt_context
):
    agri = ConversationRepository(session, agri_context)
    mgmt = ConversationRepository(session, mgmt_context)

    agri_conversation = await agri.create(agent_id="echo_agent", thread_id="thread-agri")
    mgmt_conversation = await mgmt.create(agent_id="echo_agent", thread_id="thread-mgmt")
    await session.commit()

    assert [item.id for item in await agri.list()] == [agri_conversation.id]
    assert [item.id for item in await mgmt.list()] == [mgmt_conversation.id]

    assert await mgmt.get(agri_conversation.id) is None
    with pytest.raises(ResourceNotFound):
        await mgmt.require(agri_conversation.id)


async def test_different_user_in_same_app_is_isolated(
    seeded_apps, session, agri_context, other_user_context
):
    owner = ConversationRepository(session, agri_context)
    stranger = ConversationRepository(session, other_user_context)

    conversation = await owner.create(agent_id="echo_agent", thread_id="thread-owner")
    await session.commit()

    assert await stranger.get(conversation.id) is None
    assert await stranger.list() == []


async def test_messages_require_owned_conversation(
    seeded_apps, session, agri_context, mgmt_context
):
    conversations = ConversationRepository(session, agri_context)
    conversation = await conversations.create(agent_id="echo_agent", thread_id="thread-msg")
    await session.commit()

    messages = MessageRepository(session, agri_context)
    await messages.add(conversation.id, role="user", content="hello")
    await session.commit()

    assert [message.content for message in await messages.list(conversation.id)] == ["hello"]

    stranger_messages = MessageRepository(session, mgmt_context)
    with pytest.raises(ResourceNotFound):
        await stranger_messages.list(conversation.id)


async def test_soft_deleted_conversation_disappears_from_scope(
    seeded_apps, session, agri_context
):
    repository = ConversationRepository(session, agri_context)
    conversation = await repository.create(agent_id="echo_agent", thread_id="thread-delete")
    await session.commit()

    await repository.soft_delete(conversation.id)
    await session.commit()

    assert await repository.get(conversation.id) is None
    assert await repository.list() == []
