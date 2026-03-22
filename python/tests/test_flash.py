"""
Tests for FlashMixin — put_flash / clear_flash / _drain_flash.
"""

from djust.mixins.flash import FlashMixin


class FakeView(FlashMixin):
    """Minimal view-like class for testing the mixin."""

    pass


class TestPutFlash:
    def test_put_flash_queues_message(self):
        view = FakeView()
        view.put_flash("success", "Saved!")

        assert len(view._pending_flash) == 1
        cmd = view._pending_flash[0]
        assert cmd["action"] == "put"
        assert cmd["level"] == "success"
        assert cmd["message"] == "Saved!"

    def test_multiple_flash_messages(self):
        view = FakeView()
        view.put_flash("info", "Hello")
        view.put_flash("error", "Something broke")
        view.put_flash("warning", "Watch out")

        assert len(view._pending_flash) == 3
        assert view._pending_flash[0]["level"] == "info"
        assert view._pending_flash[1]["level"] == "error"
        assert view._pending_flash[2]["level"] == "warning"

    def test_put_flash_accepts_any_level(self):
        view = FakeView()
        view.put_flash("custom-level", "Custom message")

        assert view._pending_flash[0]["level"] == "custom-level"


class TestClearFlash:
    def test_clear_flash_all(self):
        view = FakeView()
        view.clear_flash()

        assert len(view._pending_flash) == 1
        cmd = view._pending_flash[0]
        assert cmd["action"] == "clear"
        assert "level" not in cmd

    def test_clear_flash_by_level(self):
        view = FakeView()
        view.clear_flash("error")

        assert len(view._pending_flash) == 1
        cmd = view._pending_flash[0]
        assert cmd["action"] == "clear"
        assert cmd["level"] == "error"


class TestDrainFlash:
    def test_drain_flash_returns_all(self):
        view = FakeView()
        view.put_flash("success", "Done")
        view.put_flash("info", "FYI")

        result = view._drain_flash()

        assert len(result) == 2
        assert result[0]["message"] == "Done"
        assert result[1]["message"] == "FYI"

    def test_drain_flash_clears_queue(self):
        view = FakeView()
        view.put_flash("success", "Done")

        view._drain_flash()

        assert view._pending_flash == []

    def test_drain_flash_empty(self):
        view = FakeView()

        result = view._drain_flash()

        assert result == []

    def test_drain_flash_returns_mixed_commands(self):
        view = FakeView()
        view.put_flash("success", "Saved")
        view.clear_flash("error")
        view.put_flash("info", "Note")

        result = view._drain_flash()

        assert len(result) == 3
        assert result[0]["action"] == "put"
        assert result[1]["action"] == "clear"
        assert result[2]["action"] == "put"


class TestPutFlashTypeHints:
    def test_put_flash_signature(self):
        """Verify put_flash accepts level: str, message: str."""
        import inspect

        sig = inspect.signature(FlashMixin.put_flash)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "level" in params
        assert "message" in params

        level_annotation = sig.parameters["level"].annotation
        message_annotation = sig.parameters["message"].annotation
        assert level_annotation is str
        assert message_annotation is str

    def test_clear_flash_signature(self):
        """Verify clear_flash accepts level: Optional[str]."""
        import inspect

        sig = inspect.signature(FlashMixin.clear_flash)
        params = list(sig.parameters.keys())
        assert "level" in params
        assert sig.parameters["level"].default is None
