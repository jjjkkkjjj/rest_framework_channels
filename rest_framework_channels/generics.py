from __future__ import annotations

# reference: https://github.com/encode/django-rest-framework/blob/master/rest_framework/generics.py
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from asgiref.sync import async_to_sync
from django.db.models import Model, QuerySet
from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.pagination import BasePagination
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from . import mixins
from .consumers import AsyncAPIConsumer
from .handlers import AsyncAPIActionHandler
from .settings import api_settings
from .utils import request_from_scope



#: Sentinel meaning "fall back to api_settings", distinct from an explicit None.
_UNSET = object()

class GenericAsyncAPIActionHandler(AsyncAPIActionHandler):
    """
    Base class for all other generic action handlers.
    """

    # You'll need to either set these attributes,
    # or override `get_queryset()`/`get_serializer_class()`.
    # If you are overriding a view method, it is important that you call
    # `get_queryset()` instead of accessing the `queryset` property directly,
    # as `queryset` will get evaluated only once, and those results are cached
    # for all subsequent requests.
    queryset = None
    serializer_class = None

    # If you want to use object lookups other than pk, set 'lookup_field'.
    # For more complex lookup requirements override `get_object()`.
    lookup_field = 'pk'
    lookup_url_kwarg = None

    # The filter backend classes to use for queryset filtering
    filter_backends = api_settings.DEFAULT_FILTER_BACKENDS

    # The style to use for queryset pagination.
    # _UNSET (not None) is the "use the setting" sentinel, because an explicit
    # None already means "pagination disabled". Resolving in the class body
    # would freeze the setting at import time.
    pagination_class = _UNSET

    # Allow generic typing checking for generic views.
    def __class_getitem__(cls, *args, **kwargs):
        return cls

    def get_queryset(self) -> QuerySet:
        """
        Get the list of items for this view.
        This must be an iterable, and may be a queryset.
        Defaults to using `self.queryset`.

        This method should always be used rather than accessing `self.queryset`
        directly, as `self.queryset` gets evaluated only once, and those results
        are cached for all subsequent requests.

        You may want to override this if you need to provide different
        querysets depending on the incoming request.

        (Eg. return a list of items that is specific to the user)

        Returns:
            Queryset attribute.
        """
        assert self.queryset is not None, (
            f"'{self.__class__.__name__}' should either include "
            "a `queryset` attribute, or override the `get_queryset()` method."
        )

        queryset = self.queryset
        if isinstance(queryset, QuerySet):
            # Ensure queryset is re-evaluated on each request.
            queryset = queryset.all()
        return queryset

    def get_object(self, action: str) -> Model:
        """
        Returns the object the view is displaying.

        You may want to override this if you need to provide non-standard
        queryset lookups.  Eg if objects are referenced using multiple
        keyword arguments in the url conf.
        """

        queryset = self.filter_queryset(queryset=self.get_queryset())

        # Perform the lookup filtering.
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field

        assert lookup_url_kwarg in self.kwargs, (
            f'Expected aaah {self.__class__.__name__} to be called with '
            f'a URL keyword argument named "{lookup_url_kwarg}". '
            'Fix your routepettern, or set the `.lookup_field` '
            'attribute on the aaah correctly.'
        )

        filter_kwargs = {self.lookup_field: self.kwargs[lookup_url_kwarg]}

        obj = get_object_or_404(queryset, **filter_kwargs)

        # May raise a permission denied
        async_to_sync(self.check_object_permissions)(action, obj)

        return obj

    def get_serializer(self, *args, **kwargs) -> Serializer:
        """
        Return the serializer instance that should be used for validating and
        deserializing input, and for serializing output.
        """
        serializer_class = self.get_serializer_class()
        kwargs.setdefault('context', self.get_serializer_context())
        return serializer_class(*args, **kwargs)

    def get_serializer_class(self) -> type[Serializer]:
        """
        Return the class to use for the serializer.
        Defaults to using `self.serializer_class`.

        You may want to override this if you need to provide different
        serializations depending on the incoming request.

        (Eg. admins get full serialization, others get basic serialization)
        """
        assert self.serializer_class is not None, (
            f"'{self.__class__.__name__}' should either include a `serializer_class` "
            "attribute, or override the `get_serializer_class()` method."
        )

        return self.serializer_class

    def get_serializer_context(self) -> dict[str, Any]:
        """
        Extra context provided to the serializer class.
        """
        return {'scope': self.scope, 'handler': self}

    def filter_queryset(self, queryset: QuerySet) -> QuerySet:
        """
        Given a queryset, filter it with whichever filter backend is in use.

        You are unlikely to want to override this method, although you may need
        to call it either from a list view, or from a custom `get_object`
        method if you want to apply the configured filtering backend to the
        default queryset.
        """
        for backend in list(self.filter_backends):
            queryset = backend().filter_queryset(self.request, queryset, self)
        return queryset

    @property
    def paginator(self) -> BasePagination:
        """
        The paginator instance associated with the view, or `None`.
        """
        if not hasattr(self, '_paginator'):
            pagination_class = self.pagination_class
            if pagination_class is _UNSET:
                pagination_class = api_settings.DEFAULT_PAGINATION_CLASS
            if pagination_class is None:
                self._paginator = None
            else:
                self._paginator = pagination_class()
        return self._paginator

    def paginate_queryset(self, queryset: QuerySet) -> QuerySet:
        """
        Return a single page of results, or `None` if pagination is disabled.
        """
        if self.paginator is None:
            return None

        request = request_from_scope(self.scope)

        return self.paginator.paginate_queryset(queryset, request, view=self)

    def _modify_paginated_route(self, url: Optional[str]) -> Optional[str]:
        """Modify the url returned by a paginator for action handler

        Parameters
        ----------
        url : Optional[str]
            The url returned by a paginator

        Returns
        -------
        Optional[str]
            The modified route with query params
        """
        if url is None:
            return None
        route = self.scope.get('route', '')
        parsed_url = urlparse(url)
        parsed_route = urlparse(route)
        query = parsed_url.query
        if query:
            query = f'?{query}'
        return f'{parsed_route.path}{query}'

    def get_paginated_response(self, data: Any):
        """
        Return a paginated style `Response` object for the given output data.
        """
        assert self.paginator is not None
        response: Response = self.paginator.get_paginated_response(data)
        response_data = response.data

        # modify the previous and the next url into route
        response_data['next'] = self._modify_paginated_route(response_data['next'])
        response_data['previous'] = self._modify_paginated_route(
            response_data['previous']
        )

        return response_data, status.HTTP_200_OK


class CreateAPIActionHandler(mixins.CreateModelMixin, GenericAsyncAPIActionHandler):
    pass


class ListAPIActionHandler(mixins.ListModelMixin, GenericAsyncAPIActionHandler):
    pass


class RetrieveAPIActionHandler(mixins.RetrieveModelMixin, GenericAsyncAPIActionHandler):
    pass


class UpdateAPIActionHandler(mixins.UpdateModelMixin, GenericAsyncAPIActionHandler):
    pass


class DestroyAPIActionHandler(mixins.DestroyModelMixin, GenericAsyncAPIActionHandler):
    pass


class ListCreateAPIActionHandler(
    mixins.ListModelMixin, mixins.CreateModelMixin, GenericAsyncAPIActionHandler
):
    pass


class RetrieveUpdateAPIActionHandler(
    mixins.RetrieveModelMixin, mixins.UpdateModelMixin, GenericAsyncAPIActionHandler
):
    pass


class RetrieveDestroyAPIActionHandler(
    mixins.RetrieveModelMixin, mixins.DestroyModelMixin, GenericAsyncAPIActionHandler
):
    pass


class RetrieveUpdateDestroyAPIActionHandler(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    GenericAsyncAPIActionHandler,
):
    pass


'''
======= Consumer ========
'''


class GenericAsyncAPIConsumer(AsyncAPIConsumer, GenericAsyncAPIActionHandler):
    pass


class CreateAPIConsumer(mixins.CreateModelMixin, GenericAsyncAPIConsumer):
    pass


class ListAPIConsumer(mixins.ListModelMixin, GenericAsyncAPIConsumer):
    pass


class RetrieveAPIConsumer(mixins.RetrieveModelMixin, GenericAsyncAPIConsumer):
    pass


class UpdateAPIConsumer(mixins.UpdateModelMixin, GenericAsyncAPIConsumer):
    pass


class DestroyAPIConsumer(mixins.DestroyModelMixin, GenericAsyncAPIConsumer):
    pass


class ListCreateAPIConsumer(
    mixins.ListModelMixin, mixins.CreateModelMixin, GenericAsyncAPIConsumer
):
    pass


class RetrieveUpdateAPIConsumer(
    mixins.RetrieveModelMixin, mixins.UpdateModelMixin, GenericAsyncAPIConsumer
):
    pass


class RetrieveDestroyAPIConsumer(
    mixins.RetrieveModelMixin, mixins.DestroyModelMixin, GenericAsyncAPIConsumer
):
    pass


class RetrieveUpdateDestroyAPIConsumer(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    GenericAsyncAPIConsumer,
):
    pass
