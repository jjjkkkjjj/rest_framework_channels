from __future__ import annotations

from typing import Union

from channels.db import database_sync_to_async
from django.utils.translation import gettext_lazy as _
from rest_framework import exceptions, status
from rest_framework.views import set_rollback


class ActionMissingException(exceptions.APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = _('Unable to find action in message body.')
    default_code = 'not_found'


class RouteMissingException(exceptions.APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = _('Unable to find route in message body.')
    default_code = 'not_found'


class ActionNotAllowed(exceptions.MethodNotAllowed):
    status_code = status.HTTP_405_METHOD_NOT_ALLOWED
    default_detail = _('Action "{action}" not allowed in message body.')
    default_code = 'method_not_allowed'

    def __init__(self, action, detail=None, code=None):
        if detail is None:
            detail = f'Action "{action}" not allowed in message body.'
        self.detail = detail


async def debug_exception_handlers(
    exc: Union[
        ActionNotAllowed,
        exceptions.PermissionDenied,
        exceptions.APIException,
        Exception,
    ],
    context: dict,
) -> None:
    """Do nothing at all"""
    return None


async def production_exception_handlers(
    exc: Union[
        ActionNotAllowed,
        exceptions.PermissionDenied,
        exceptions.APIException,
        Exception,
    ],
    context: dict,
) -> dict:
    """
    Returns the response that should be used for any given exception.

    By default we handle the REST framework `exceptions.APIException`, and also
    Django's built-in `Http404` and `PermissionDenied` exceptions.

    Any unhandled exceptions may return `None`, which will cause a 500 error
    to be raised.
    """
    # NOTE: ActionNotAllowed is intentionally not remapped here. It never
    # reaches this function -- handle_exception short-circuits it -- and the old
    # mapping downgraded 405 to 404 while dropping the detail, because
    # ActionNotAllowed.__init__ never populates `args`.
    if isinstance(exc, exceptions.PermissionDenied):
        exc = exceptions.PermissionDenied(*(exc.args))

    if isinstance(exc, exceptions.APIException):
        context.update(dict(status=exc.status_code))
        if isinstance(exc.detail, list):
            errors = exc.detail
        elif isinstance(exc.detail, dict):
            errors = [exc.detail]
        else:
            errors = [{'detail': exc.detail}]
    else:
        # Other exception such like ValueError
        context.update(dict(status=int(exceptions.APIException.status_code)))
        errors = [{'detail': str(exceptions.APIException.default_detail)}]

    await database_sync_to_async(set_rollback)()
    return dict(**context, errors=errors)
