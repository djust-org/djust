"""
Tests for async event handler support in WebSocket consumer.

Verifies that both sync and async event handlers work correctly.
Regression test for issue #60.
"""
import pytest
from djust.websocket import _call_handler


# Track handler calls for verification
call_log = []


def sync_handler():
    """A sync handler with no params."""
    call_log.append(('sync_handler', None))
    return 'sync_no_params'


def sync_handler_with_params(item_id: int, name: str):
    """A sync handler with params."""
    call_log.append(('sync_handler_with_params', {'item_id': item_id, 'name': name}))
    return f'sync_{item_id}_{name}'


async def async_handler():
    """An async handler with no params."""
    call_log.append(('async_handler', None))
    return 'async_no_params'


async def async_handler_with_params(item_id: int, name: str):
    """An async handler with params."""
    call_log.append(('async_handler_with_params', {'item_id': item_id, 'name': name}))
    return f'async_{item_id}_{name}'


@pytest.fixture(autouse=True)
def reset_call_log():
    """Reset call log before each test."""
    global call_log
    call_log = []
    yield
    call_log = []


class TestCallHandler:
    """Tests for _call_handler helper function."""

    @pytest.mark.asyncio
    async def test_sync_handler_no_params(self):
        """Sync handler without params should work."""
        result = await _call_handler(sync_handler)
        assert result == 'sync_no_params'
        assert call_log == [('sync_handler', None)]

    @pytest.mark.asyncio
    async def test_sync_handler_with_params(self):
        """Sync handler with params should work."""
        result = await _call_handler(sync_handler_with_params, {'item_id': 42, 'name': 'test'})
        assert result == 'sync_42_test'
        assert call_log == [('sync_handler_with_params', {'item_id': 42, 'name': 'test'})]

    @pytest.mark.asyncio
    async def test_async_handler_no_params(self):
        """Async handler without params should work (regression test for #60)."""
        result = await _call_handler(async_handler)
        assert result == 'async_no_params'
        assert call_log == [('async_handler', None)]

    @pytest.mark.asyncio
    async def test_async_handler_with_params(self):
        """Async handler with params should work (regression test for #60)."""
        result = await _call_handler(async_handler_with_params, {'item_id': 99, 'name': 'async_test'})
        assert result == 'async_99_async_test'
        assert call_log == [('async_handler_with_params', {'item_id': 99, 'name': 'async_test'})]

    @pytest.mark.asyncio
    async def test_none_params_calls_without_args(self):
        """Passing None for params should call handler without arguments."""
        result = await _call_handler(sync_handler, None)
        assert result == 'sync_no_params'

    @pytest.mark.asyncio
    async def test_empty_dict_params_calls_without_args(self):
        """Passing empty dict should call handler without arguments (falsy check)."""
        result = await _call_handler(sync_handler, {})
        assert result == 'sync_no_params'
