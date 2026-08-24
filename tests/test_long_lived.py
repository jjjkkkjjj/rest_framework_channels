"""Tests for behaviour that only shows up over a long-lived connection.

The rest of the suite sends at most three messages per connection, so anything
that accumulates per message -- channel names, group members, scope keys --
stayed invisible. These tests drive hundreds of messages down a single socket.
"""

from __future__ import annotations

import time

import pytest
from django.urls import path

from rest_framework_channels.consumers import AsyncAPIConsumer
from rest_framework_channels.decorators import async_action
from rest_framework_channels.handlers import AsyncAPIActionHandler
from rest_framework_channels.testing.websocket import AuthCommunicator

#: How many messages one connection sends. Large enough that per-message
#: accumulation becomes obvious, small enough to keep the suite quick.
MESSAGE_COUNT = 500


class EchoHandler(AsyncAPIActionHandler):
    @async_action()
    async def echo(self, seq: int = 0, **kwargs):
        return {'seq': seq}, 200


class EchoConsumer(AsyncAPIConsumer):
    group_send_lookup_kwargs = 'group_id'

    routepatterns = [path('echo/', EchoHandler.as_aaah())]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_many_messages_on_one_connection(user):
    """Several hundred round trips keep working and stay in order."""
    communicator = AuthCommunicator(
        user,
        EchoConsumer(),
        '/testws/1234-5678/',
        kwargs=dict(group_id='1234-5678'),
    )
    connected, _ = await communicator.connect()
    assert connected

    for seq in range(MESSAGE_COUNT):
        await communicator.send_json_to(
            {'action': 'echo', 'route': 'echo/', 'seq': seq}
        )
        response = await communicator.receive_json_from()
        assert response['status'] == 200, f'failed at message {seq}: {response}'
        assert response['data']['seq'] == seq
        # The reply must echo the route the client sent, not an internal
        # remainder, or the client cannot correlate it.
        assert response['route'] == 'echo/'

    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_per_message_cost_does_not_grow(user):
    """Message N must not be dramatically slower than message 1.

    A per-message leak (group members, channel queues, scope keys) shows up as
    steadily rising latency long before it shows up as a wrong answer. Without
    this guard the failure surfaces only in a downstream project as a timeout.
    """
    communicator = AuthCommunicator(
        user,
        EchoConsumer(),
        '/testws/1234-5678/',
        kwargs=dict(group_id='1234-5678'),
    )
    connected, _ = await communicator.connect()
    assert connected

    async def round_trip(seq: int) -> float:
        started = time.perf_counter()
        await communicator.send_json_to(
            {'action': 'echo', 'route': 'echo/', 'seq': seq}
        )
        await communicator.receive_json_from()
        return time.perf_counter() - started

    # Warm up so that first-call import/compile costs don't skew the baseline.
    for seq in range(20):
        await round_trip(seq)

    first = sum([await round_trip(seq) for seq in range(20, 40)]) / 20

    for seq in range(40, MESSAGE_COUNT):
        await round_trip(seq)

    last = sum(
        [await round_trip(seq) for seq in range(MESSAGE_COUNT, MESSAGE_COUNT + 20)]
    ) / 20

    await communicator.disconnect()

    # Generous: this is meant to catch runaway growth, not ordinary jitter.
    assert last < first * 10 + 0.01, (
        f'per-message cost grew from {first:.6f}s to {last:.6f}s over '
        f'{MESSAGE_COUNT} messages'
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_group_membership_does_not_grow_per_message(user):
    """The group holds one channel per connection, not one per message."""
    communicator = AuthCommunicator(
        user,
        EchoConsumer(),
        '/testws/1234-5678/',
        kwargs=dict(group_id='1234-5678'),
    )
    connected, _ = await communicator.connect()
    assert connected

    from channels.layers import get_channel_layer

    channel_layer = get_channel_layer()

    for seq in range(50):
        await communicator.send_json_to(
            {'action': 'echo', 'route': 'echo/', 'seq': seq}
        )
        await communicator.receive_json_from()

    assert len(channel_layer.groups.get('1234-5678', {})) == 1

    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_query_params_do_not_leak_between_messages(user):
    """A query string on one message must not reappear on the next."""
    seen = []

    class QueryHandler(AsyncAPIActionHandler):
        @async_action()
        async def probe(self, **kwargs):
            seen.append(self.scope.get('path_remaining'))
            return {}, 200

    class QueryConsumer(AsyncAPIConsumer):
        routepatterns = [path('probe/', QueryHandler.as_aaah())]

        @async_action()
        async def local_probe(self, **kwargs):
            seen.append(self.scope.get('path_remaining'))
            return {}, 200

    communicator = AuthCommunicator(user, QueryConsumer(), '/testws/')
    connected, _ = await communicator.connect()
    assert connected

    # The consumer handles this itself (no route matches), which is the path
    # that used to write back into the long-lived connection scope.
    await communicator.send_json_to({'action': 'local_probe', 'route': '?page=4'})
    await communicator.receive_json_from()

    await communicator.send_json_to({'action': 'local_probe', 'route': ''})
    await communicator.receive_json_from()

    await communicator.disconnect()

    assert seen[0] == 'page=4'
    assert not seen[1], f'query leaked into the next message: {seen[1]!r}'
