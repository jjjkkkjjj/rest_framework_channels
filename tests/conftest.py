from __future__ import annotations

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model


@database_sync_to_async
def create_user():
    return get_user_model().objects.create_user(username='test', password='password')


@pytest.fixture
def user():
    return async_to_sync(create_user)()


@pytest.fixture
def debug_exception_handler(settings):
    # The handler is resolved per exception, so overriding the setting is enough.
    settings.REST_FRAMEWORK_CHANNELS = {
        'EXCEPTION_HANDLER': (
            'rest_framework_channels.exceptions.debug_exception_handlers'
        )
    }
    yield
