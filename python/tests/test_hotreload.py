"""
Unit tests for hot reload functionality.

Tests the hot reload flow including template cache clearing,
patch generation, error handling, and performance metrics.
"""

import pytest
import json
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from django.template import TemplateDoesNotExist


@pytest.fixture
def mock_view_instance():
    """Create a mock LiveView instance for testing."""
    view = Mock()
    view.get_template = Mock(return_value=Mock())
    view.render_with_diff = Mock(return_value=("<html>test</html>", [], 1))
    return view


@pytest.fixture
def mock_consumer():
    """Create a mock LiveViewConsumer for testing."""
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.send_json = AsyncMock()
    consumer.view_instance = None
    return consumer


class TestClearTemplateCaches:
    """Tests for _clear_template_caches() static method."""

    def test_clear_template_caches_with_django_engine(self):
        """Test clearing Django template engine caches."""
        from djust.websocket import LiveViewConsumer

        with patch('django.template.engines') as mock_engines:
            # Mock Django template engine
            mock_loader = Mock()
            mock_loader.reset = Mock()

            mock_engine = Mock()
            mock_engine.name = "django"
            mock_engine.engine = Mock()
            mock_engine.engine.template_loaders = [mock_loader]

            mock_engines.all.return_value = [mock_engine]

            # Clear caches
            count = LiveViewConsumer._clear_template_caches()

            # Verify
            assert count == 1
            mock_loader.reset.assert_called_once()

    def test_clear_template_caches_no_loaders(self):
        """Test clearing caches when no loaders have reset()."""
        from djust.websocket import LiveViewConsumer

        with patch('django.template.engines') as mock_engines:
            # Mock engine without template_loaders
            mock_engine = Mock()
            mock_engine.name = "django"
            mock_engine.engine = Mock(spec=[])  # No template_loaders attribute

            mock_engines.all.return_value = [mock_engine]

            # Clear caches (should not fail)
            count = LiveViewConsumer._clear_template_caches()

            # Should return 0 (no caches cleared)
            assert count == 0

    def test_clear_template_caches_handles_exception(self):
        """Test that cache clearing handles exceptions gracefully."""
        from djust.websocket import LiveViewConsumer

        with patch('django.template.engines') as mock_engines:
            # Mock loader that raises exception
            mock_loader = Mock()
            mock_loader.reset = Mock(side_effect=Exception("Test error"))

            mock_engine = Mock()
            mock_engine.name = "django"
            mock_engine.engine = Mock()
            mock_engine.engine.template_loaders = [mock_loader]

            mock_engines.all.return_value = [mock_engine]

            # Should not raise exception
            count = LiveViewConsumer._clear_template_caches()

            # Should return 0 (cache clearing failed)
            assert count == 0

    def test_clear_template_caches_multiple_engines(self):
        """Test clearing caches across multiple template engines."""
        from djust.websocket import LiveViewConsumer

        with patch('django.template.engines') as mock_engines:
            # Mock multiple engines with loaders
            loaders = []
            engines = []

            for i in range(3):
                mock_loader = Mock()
                mock_loader.reset = Mock()
                loaders.append(mock_loader)

                mock_engine = Mock()
                mock_engine.name = f"engine{i}"
                mock_engine.engine = Mock()
                mock_engine.engine.template_loaders = [mock_loader]
                engines.append(mock_engine)

            mock_engines.all.return_value = engines

            # Clear caches
            count = LiveViewConsumer._clear_template_caches()

            # Verify all loaders reset
            assert count == 3
            for loader in loaders:
                loader.reset.assert_called_once()


class TestHotReloadMessage:
    """Tests for hotreload_message() method."""

    @pytest.mark.asyncio
    async def test_hotreload_successful_patch_generation(self, mock_consumer, mock_view_instance):
        """Test successful hot reload with patch generation."""
        mock_consumer.view_instance = mock_view_instance

        # Mock template and patches
        patches = [
            {"op": "replace", "path": [0, 0], "value": "Updated text"}
        ]
        patches_json = json.dumps(patches)
        mock_view_instance.render_with_diff = Mock(
            return_value=("<html>new</html>", patches_json, 2)
        )

        with patch.object(mock_consumer, '_clear_template_caches', return_value=1):
            with patch('channels.db.database_sync_to_async', side_effect=lambda f: AsyncMock(return_value=f())):
                # Call hotreload_message
                await mock_consumer.hotreload_message({"file": "test.html"})

        # Verify patches sent to client
        mock_consumer.send_json.assert_called_once()
        call_args = mock_consumer.send_json.call_args[0][0]

        assert call_args["type"] == "patch"
        assert call_args["patches"] == patches
        assert call_args["version"] == 2
        assert call_args["hotreload"] is True
        assert call_args["file"] == "test.html"

    @pytest.mark.asyncio
    async def test_hotreload_template_not_found(self, mock_consumer, mock_view_instance):
        """Test hot reload when template doesn't exist."""
        mock_consumer.view_instance = mock_view_instance

        # Mock template not found
        mock_view_instance.get_template = Mock(side_effect=TemplateDoesNotExist("test.html"))

        with patch.object(mock_consumer, '_clear_template_caches', return_value=1):
            with patch('channels.db.database_sync_to_async', side_effect=lambda f: AsyncMock(return_value=f())):
                # Call hotreload_message
                await mock_consumer.hotreload_message({"file": "test.html"})

        # Verify fallback to full reload
        mock_consumer.send_json.assert_called_once()
        call_args = mock_consumer.send_json.call_args[0][0]

        assert call_args["type"] == "reload"
        assert call_args["file"] == "test.html"

    @pytest.mark.asyncio
    async def test_hotreload_json_decode_error(self, mock_consumer, mock_view_instance):
        """Test hot reload when patch JSON is invalid."""
        mock_consumer.view_instance = mock_view_instance

        # Mock invalid JSON patches
        mock_view_instance.render_with_diff = Mock(
            return_value=("<html>new</html>", "invalid json", 2)
        )

        with patch.object(mock_consumer, '_clear_template_caches', return_value=1):
            with patch('channels.db.database_sync_to_async', side_effect=lambda f: AsyncMock(return_value=f())):
                # Call hotreload_message
                await mock_consumer.hotreload_message({"file": "test.html"})

        # Verify fallback to full reload
        mock_consumer.send_json.assert_called_once()
        call_args = mock_consumer.send_json.call_args[0][0]

        assert call_args["type"] == "reload"
        assert call_args["file"] == "test.html"

    @pytest.mark.asyncio
    async def test_hotreload_no_patches(self, mock_consumer, mock_view_instance):
        """Test hot reload when no patches are generated."""
        mock_consumer.view_instance = mock_view_instance

        # Mock empty patches
        mock_view_instance.render_with_diff = Mock(
            return_value=("<html>same</html>", None, 1)
        )

        with patch.object(mock_consumer, '_clear_template_caches', return_value=1):
            with patch('channels.db.database_sync_to_async', side_effect=lambda f: AsyncMock(return_value=f())):
                # Call hotreload_message
                await mock_consumer.hotreload_message({"file": "test.html"})

        # Verify fallback to full reload (no patches to apply)
        mock_consumer.send_json.assert_called_once()
        call_args = mock_consumer.send_json.call_args[0][0]

        assert call_args["type"] == "reload"
        assert call_args["file"] == "test.html"

    @pytest.mark.asyncio
    async def test_hotreload_empty_patches_array(self, mock_consumer, mock_view_instance):
        """Test hot reload when patches array is empty."""
        mock_consumer.view_instance = mock_view_instance

        # Mock empty patches array (gets parsed but still sent as patch)
        mock_view_instance.render_with_diff = Mock(
            return_value=("<html>same</html>", "[]", 1)
        )

        with patch.object(mock_consumer, '_clear_template_caches', return_value=1):
            with patch('channels.db.database_sync_to_async', side_effect=lambda f: AsyncMock(return_value=f())):
                # Call hotreload_message
                await mock_consumer.hotreload_message({"file": "test.html"})

        # Empty array is still sent as patch (no check after parsing)
        mock_consumer.send_json.assert_called_once()
        call_args = mock_consumer.send_json.call_args[0][0]

        assert call_args["type"] == "patch"
        assert call_args["patches"] == []
        assert call_args["file"] == "test.html"

    @pytest.mark.asyncio
    async def test_hotreload_general_exception(self, mock_consumer, mock_view_instance):
        """Test hot reload with unexpected exception."""
        mock_consumer.view_instance = mock_view_instance

        # Mock general exception during render
        mock_view_instance.render_with_diff = Mock(
            side_effect=Exception("Unexpected error")
        )

        with patch.object(mock_consumer, '_clear_template_caches', return_value=1):
            with patch('channels.db.database_sync_to_async', side_effect=lambda f: AsyncMock(return_value=f())):
                # Call hotreload_message
                await mock_consumer.hotreload_message({"file": "test.html"})

        # Verify fallback to full reload
        mock_consumer.send_json.assert_called_once()
        call_args = mock_consumer.send_json.call_args[0][0]

        assert call_args["type"] == "reload"
        assert call_args["file"] == "test.html"

    @pytest.mark.asyncio
    async def test_hotreload_no_view_instance(self, mock_consumer):
        """Test hot reload when view_instance is None."""
        mock_consumer.view_instance = None

        # Call hotreload_message (should handle gracefully)
        await mock_consumer.hotreload_message({"file": "test.html"})

        # Should send full reload (no view instance to generate patches)
        mock_consumer.send_json.assert_called_once()
        call_args = mock_consumer.send_json.call_args[0][0]
        assert call_args["type"] == "reload"
        assert call_args["file"] == "test.html"

    @pytest.mark.asyncio
    async def test_hotreload_performance_logging(self, mock_consumer, mock_view_instance):
        """Test that performance metrics are logged."""
        mock_consumer.view_instance = mock_view_instance

        # Mock patches
        patches = [{"op": "replace", "path": [0], "value": "test"}]
        mock_view_instance.render_with_diff = Mock(
            return_value=("<html>new</html>", json.dumps(patches), 2)
        )

        with patch.object(mock_consumer, '_clear_template_caches', return_value=1):
            with patch('channels.db.database_sync_to_async', side_effect=lambda f: AsyncMock(return_value=f())):
                with patch('logging.getLogger') as mock_logger:
                    mock_log = Mock()
                    mock_logger.return_value = mock_log

                    # Call hotreload_message
                    await mock_consumer.hotreload_message({"file": "test.html"})

                    # Verify performance logging
                    assert mock_log.info.called
                    # Check that info was called with patch count and timing info
                    info_calls = [str(call) for call in mock_log.info.call_args_list]
                    assert any("patches" in str(call) for call in info_calls)

    @pytest.mark.asyncio
    async def test_hotreload_slow_patch_warning(self, mock_consumer, mock_view_instance):
        """Test that slow patch generation triggers warning."""
        mock_consumer.view_instance = mock_view_instance

        # Mock slow render (simulate by patching time.time)
        patches = [{"op": "replace", "path": [0], "value": "test"}]
        mock_view_instance.render_with_diff = Mock(
            return_value=("<html>new</html>", json.dumps(patches), 2)
        )

        with patch.object(mock_consumer, '_clear_template_caches', return_value=1):
            with patch('channels.db.database_sync_to_async', side_effect=lambda f: AsyncMock(return_value=f())):
                with patch('time.time') as mock_time:
                    # Simulate >100ms render time
                    # Calls: start_time, render_start, render_end, total_end
                    mock_time.side_effect = [0.0, 0.0, 0.15, 0.15]

                    with patch('logging.getLogger') as mock_logger:
                        mock_log = Mock()
                        mock_logger.return_value = mock_log

                        # Call hotreload_message
                        await mock_consumer.hotreload_message({"file": "test.html"})

                        # Verify warning logged for slow patch generation
                        assert mock_log.warning.called
                        warning_calls = [str(call) for call in mock_log.warning.call_args_list]
                        assert any("Slow patch generation" in str(call) for call in warning_calls)


class TestHotReloadIntegration:
    """Integration tests for complete hot reload flow."""

    @pytest.mark.asyncio
    async def test_complete_hotreload_flow(self, mock_consumer, mock_view_instance):
        """Test complete hot reload flow from file change to client update."""
        mock_consumer.view_instance = mock_view_instance

        # Mock a realistic scenario
        new_template = Mock()
        mock_view_instance.get_template = Mock(return_value=new_template)

        patches = [
            {"op": "replace", "path": [0, 1, 0], "value": "Updated heading"},
            {"op": "replace", "path": [0, 2, 1], "value": "New paragraph"}
        ]
        mock_view_instance.render_with_diff = Mock(
            return_value=(
                "<html><body><h1>Updated heading</h1><p>New paragraph</p></body></html>",
                json.dumps(patches),
                3
            )
        )

        with patch.object(mock_consumer, '_clear_template_caches', return_value=2):
            with patch('channels.db.database_sync_to_async', side_effect=lambda f: AsyncMock(return_value=f())):
                # Simulate file change event
                event = {"file": "templates/index.html"}

                # Call hotreload_message
                await mock_consumer.hotreload_message(event)

        # Verify complete flow
        # 1. Template cache cleared
        # 2. New template loaded
        mock_view_instance.get_template.assert_called_once()

        # 3. Patches generated
        mock_view_instance.render_with_diff.assert_called_once()

        # 4. Patches sent to client
        mock_consumer.send_json.assert_called_once()
        call_args = mock_consumer.send_json.call_args[0][0]

        assert call_args["type"] == "patch"
        assert call_args["patches"] == patches
        assert call_args["version"] == 3
        assert call_args["hotreload"] is True
        assert call_args["file"] == "templates/index.html"

    @pytest.mark.asyncio
    async def test_hotreload_with_cached_template_attribute(self, mock_consumer, mock_view_instance):
        """Test that cached _template attribute is cleared."""
        mock_consumer.view_instance = mock_view_instance

        # Set cached _template attribute
        mock_view_instance._template = Mock()

        patches = [{"op": "replace", "path": [0], "value": "test"}]
        mock_view_instance.render_with_diff = Mock(
            return_value=("<html>new</html>", json.dumps(patches), 2)
        )

        with patch.object(mock_consumer, '_clear_template_caches', return_value=1):
            with patch('channels.db.database_sync_to_async', side_effect=lambda f: AsyncMock(return_value=f())):
                # Call hotreload_message
                await mock_consumer.hotreload_message({"file": "test.html"})

        # Verify _template was cleared
        assert not hasattr(mock_view_instance, '_template')

        # Verify patches sent
        mock_consumer.send_json.assert_called_once()
        call_args = mock_consumer.send_json.call_args[0][0]
        assert call_args["type"] == "patch"


class TestHotReloadEdgeCases:
    """Tests for edge cases in hot reload functionality."""

    @pytest.mark.asyncio
    async def test_hotreload_missing_file_key(self, mock_consumer, mock_view_instance):
        """Test hot reload with missing 'file' key in event."""
        mock_consumer.view_instance = mock_view_instance

        patches = [{"op": "replace", "path": [0], "value": "test"}]
        mock_view_instance.render_with_diff = Mock(
            return_value=("<html>new</html>", json.dumps(patches), 2)
        )

        with patch.object(mock_consumer, '_clear_template_caches', return_value=1):
            with patch('channels.db.database_sync_to_async', side_effect=lambda f: AsyncMock(return_value=f())):
                # Call with empty event
                await mock_consumer.hotreload_message({})

        # Should still work, file defaults to "unknown"
        mock_consumer.send_json.assert_called_once()
        call_args = mock_consumer.send_json.call_args[0][0]
        assert call_args["file"] == "unknown"

    @pytest.mark.asyncio
    async def test_hotreload_patches_as_array(self, mock_consumer, mock_view_instance):
        """Test hot reload when patches are already an array (not JSON string)."""
        mock_consumer.view_instance = mock_view_instance

        # Mock patches as array (not JSON string)
        patches = [{"op": "replace", "path": [0], "value": "test"}]
        mock_view_instance.render_with_diff = Mock(
            return_value=("<html>new</html>", patches, 2)  # Array, not JSON
        )

        with patch.object(mock_consumer, '_clear_template_caches', return_value=1):
            with patch('channels.db.database_sync_to_async', side_effect=lambda f: AsyncMock(return_value=f())):
                # Call hotreload_message
                await mock_consumer.hotreload_message({"file": "test.html"})

        # Should handle array patches correctly
        mock_consumer.send_json.assert_called_once()
        call_args = mock_consumer.send_json.call_args[0][0]
        assert call_args["type"] == "patch"
        assert call_args["patches"] == patches

    @pytest.mark.asyncio
    async def test_hotreload_unicode_file_path(self, mock_consumer, mock_view_instance):
        """Test hot reload with unicode characters in file path."""
        mock_consumer.view_instance = mock_view_instance

        patches = [{"op": "replace", "path": [0], "value": "test"}]
        mock_view_instance.render_with_diff = Mock(
            return_value=("<html>new</html>", json.dumps(patches), 2)
        )

        with patch.object(mock_consumer, '_clear_template_caches', return_value=1):
            with patch('channels.db.database_sync_to_async', side_effect=lambda f: AsyncMock(return_value=f())):
                # Call with unicode file path
                await mock_consumer.hotreload_message({"file": "templates/编辑.html"})

        # Should handle unicode correctly
        mock_consumer.send_json.assert_called_once()
        call_args = mock_consumer.send_json.call_args[0][0]
        assert call_args["file"] == "templates/编辑.html"
