from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, NamedTuple, Optional

from django.urls import URLPattern
from django.urls.exceptions import Resolver404

from .exceptions import RouteMissingException

if TYPE_CHECKING:
    from .handlers import AsyncActionHandler

logger = logging.getLogger(__name__)


class RoutingEntry(NamedTuple):
    """A registered route and the metadata its parent attached to it."""

    pattern: URLPattern
    parent_group_send_lookup_kwargs: Optional[str] = None


class RoutingManager:
    def __init__(self):
        self.entries: list[RoutingEntry] = []

    @property
    def routes(self) -> list[URLPattern]:
        """The registered URLPatterns.

        Kept for backward compatibility; ``entries`` is the source of truth.
        """
        return [entry.pattern for entry in self.entries]

    def append(
        self,
        pattern: URLPattern,
        parent_group_send_lookup_kwargs: Optional[str] = None,
    ) -> None:
        """Add new route to be managed

        Parameters
        ----------
        pattern: URLPattern
            The URLPattern instance
        parent_group_send_lookup_kwargs: Optional[str]
            The parent's ``group_send_lookup_kwargs``, propagated to the child
            handler through the scope rather than by mutating the child class.
        """
        self.entries.append(RoutingEntry(pattern, parent_group_send_lookup_kwargs))

    # Any is intended for avoiding vscode's testing error due to circular import
    async def resolve(
        self,
        route: str,
        scope: dict,
        receive: Callable,
        send: Callable,
    ) -> AsyncActionHandler:
        """Resolve a given route

        Parameters
        ----------
        route : str
            The route path to be resolved
        scope : dict
            The scope dict
        receive : Callable
            The recieve function to be passed into a matched action handler
        send : Callable
            The send function to be passed into a matched action handler

        Returns
        -------
        AsyncActionHandler
            The matched action handler

        Raises
        ------
        RouteMissingException
            When no registered pattern matches ``route``. Callers treat this as
            "handle the action locally", so it is normal control flow rather
            than a failure -- see ``AsyncAPIActionHandler.handle_action``.
        """

        matched: Optional[tuple[RoutingEntry, tuple]] = None
        for entry in self.entries:
            try:
                match = entry.pattern.pattern.match(route)
            except Resolver404:
                # Only pattern matching is guarded here. Swallowing anything the
                # handler itself raises would turn a real failure into a
                # misleading "no route found".
                logger.warning(
                    'Pattern %r raised Resolver404 while matching route %r',
                    entry.pattern,
                    route,
                )
                continue
            if match:
                matched = (entry, match)
                break

        if matched is None:
            raise RouteMissingException(f'No route found: {route}')

        entry, (new_path, args, kwargs) = matched

        # Add args or kwargs into the scope
        outer = scope.get('url_route', {})
        handler = entry.pattern.callback

        return await handler(
            dict(
                scope,
                path_remaining=new_path,
                url_route={
                    'args': outer.get('args', ()) + args,
                    'kwargs': {**outer.get('kwargs', {}), **kwargs},
                },
                # Keep the route the client sent, even three levels down, so
                # replies echo something the client can correlate.
                route=scope.get('route') or route,
                parent_group_send_lookup_kwargs=(
                    entry.parent_group_send_lookup_kwargs
                ),
            ),
            receive,
            send,
        )
