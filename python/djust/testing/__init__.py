"""Re-export public API from the testing.py module (sibling of this package dir)."""
import importlib.util as _ilu
from pathlib import Path as _P

# testing.py lives next to the testing/ directory
_testing_py = _P(__file__).resolve().parent.with_suffix(".py")

_spec = _ilu.spec_from_file_location("djust._testing_impl", str(_testing_py))

if _spec and _spec.loader:
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)

    LiveViewTestClient = _mod.LiveViewTestClient
    ComponentTestClient = _mod.ComponentTestClient
    MockUploadFile = _mod.MockUploadFile
    MockRequest = _mod.MockRequest
    SnapshotTestMixin = _mod.SnapshotTestMixin
    _BaseLiveViewTestClient = _mod._BaseLiveViewTestClient
    performance_test = _mod.performance_test
    create_test_view = _mod.create_test_view
