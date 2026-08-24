"""Tests for error and fallback paths that had no coverage.

Each of these exercised a code path that was broken in released code: an
unknown action name, a message with no action at all, the default broadcast
type, and routing nested more than one level deep.
"""

from __future__ import annotations

import pytest
from django.urls import path

from rest_framework_channels.consumers import AsyncAPIConsumer
from rest_framework_channels.decorators import async_action
from rest_framework_channels.handlers import AsyncAPIActionHandler
from rest_framework_channels.testing.websocket import AuthCommunicator


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_unknown_action_returns_405(user):
    """An action nobody defines comes back as ActionNotAllowed, not a crash."""

    class Consumer(AsyncAPIConsumer):
        @async_action()
        async def known(self, **kwargs):
            return {}, 200

    communicator = AuthCommunicator(user, Consumer(), '/testws/')
    connected, _ = await communicator.connect()
    assert connected

    await communicator.send_json_to({'action': 'nonexistent', 'route': ''})
    response = await communicator.receive_json_from()

    assert response['status'] == 405
    assert 'nonexistent' in response['errors'][0]

    # The connection must survive a bad action.
    await communicator.send_json_to({'action': 'known', 'route': ''})
    assert (await communicator.receive_json_from())['status'] == 200

    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_missing_action_key_does_not_kill_the_connection(user):
    """A message with no 'action' replies with an error instead of crashing.

    This used to raise TypeError inside the receive loop, because
    handle_exception was called without its required `route` argument.
    """

    class Consumer(AsyncAPIConsumer):
        @async_action()
        async def known(self, **kwargs):
            return {}, 200

    communicator = AuthCommunicator(user, Consumer(), '/testws/')
    connected, _ = await communicator.connect()
    assert connected

    await communicator.send_json_to({'route': ''})
    response = await communicator.receive_json_from()
    assert response['status'] == 404

    await communicator.send_json_to({'action': 'known', 'route': ''})
    assert (await communicator.receive_json_from())['status'] == 200

    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_default_broadcast_type_is_dispatchable(user):
    """mode='broadcast' without an explicit type must reach the consumer.

    Channels refuses to dispatch a message whose handler name would start with
    an underscore, so the old default could never be delivered -- and the
    resulting ValueError killed every connection in the group.
    """

    class Handler(AsyncAPIActionHandler):
        @async_action(mode='broadcast')
        async def shout(self, **kwargs):
            return {'said': 'hello'}, 200

    class Consumer(AsyncAPIConsumer):
        group_send_lookup_kwargs = 'group_id'
        routepatterns = [path('shout/', Handler.as_aaah())]

    communicator = AuthCommunicator(
        user, Consumer(), '/testws/1234-5678/', kwargs=dict(group_id='1234-5678')
    )
    connected, _ = await communicator.connect()
    assert connected

    await communicator.send_json_to({'action': 'shout', 'route': 'shout/'})

    # The direct reply, then the broadcast the consumer relayed back.
    reply = await communicator.receive_json_from()
    assert reply['status'] == 200

    broadcast = await communicator.receive_json_from()
    assert broadcast['data'] == {'said': 'hello'}

    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_three_level_routing_resolves(user):
    """Routing composes past the second level.

    The parent used to hand the child the *whole* route instead of the
    remaining path, so only a leaf whose own routing was empty could ever be
    reached.
    """

    class GrandChild(AsyncAPIActionHandler):
        @async_action()
        async def deep(self, **kwargs):
            return {'level': 3}, 200

    class Child(AsyncAPIActionHandler):
        routepatterns = [path('grandchild/', GrandChild.as_aaah())]

        @async_action()
        async def deep(self, **kwargs):
            return {'level': 2}, 200

    class Parent(AsyncAPIConsumer):
        routepatterns = [path('child/', Child.as_aaah())]

        @async_action()
        async def deep(self, **kwargs):
            return {'level': 1}, 200

    communicator = AuthCommunicator(user, Parent(), '/testws/')
    connected, _ = await communicator.connect()
    assert connected

    await communicator.send_json_to(
        {'action': 'deep', 'route': 'child/grandchild/'}
    )
    response = await communicator.receive_json_from()

    assert response['data'] == {'level': 3}
    # The client gets back the route it sent, not the remaining path.
    assert response['route'] == 'child/grandchild/'

    # The intermediate level is still reachable on its own.
    await communicator.send_json_to({'action': 'deep', 'route': 'child/'})
    assert (await communicator.receive_json_from())['data'] == {'level': 2}

    await communicator.disconnect()
