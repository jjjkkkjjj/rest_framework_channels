# Broadcast

You can handle the broadcast in the `ActionHandler` as well.

See the below example first. As you can see this example, you can pass the data to the event method when you pass the arguments in the `async_action`.

```python
class ChildConsumer(AsyncAPIActionHandler):
    permission_classes = (IsAuthenticated,)

    @async_action(
        mode='broadcast',
        broadcast_type='test.called',
    )
    async def test_async_action(self, **kwargs):
        return {'test': 'content'}, 200

class ParentConsumer(AsyncAPIConsumer):
    group_send_lookup_kwargs = 'group_id'

    routepatterns = [
        path(
            'test_async_child_route/',
            ChildConsumer.as_aaah(),
        ),
    ]

    async def test_called(self, event):
        await self.send_json(event)
```

The available arguments about broadcast are here;

- `mode` : str
  - The available modes are `['response', 'broadcast', 'none']`
    - `'response'`: Send the response to the user sending this action
    - `'broadcast'`: Broadcast the response to the users in the specific group
    - `'none'`: Do nothing at all
- `broadcast_type` : str
  - The message type used for broadcasting, defaulting to `'general.broadcast'`
    (handled by `AsyncAPIConsumerBase.general_broadcast`).
  - The type maps to a handler name by replacing `.` with `_`, and channels
    rejects any name starting with an underscore. So `'_general.broadcast'` and
    any other leading-underscore type cannot be delivered -- the resulting error
    is raised inside the recipient's receive loop and drops its connection.
- `send_response_in_broadcast` : bool
  - Whether to send the response in broadcast mode, default to True
