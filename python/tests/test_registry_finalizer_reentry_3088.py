"""Registry replacement/removal must not run Python finalizers under a lock."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


# Each case runs in its own process: an under-lock __del__ deadlock must become
# a bounded failure, not strand a pytest worker or the rest of the test suite.
_CHILD = r"""
import faulthandler
import sys
import sysconfig
from djust import _rust as rust
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "free-threaded native validation requires GIL off"
faulthandler.dump_traceback_later(8)
kind, action = sys.argv[1:]
finished = []
name = "finalizer_reentry_3088"

class Handler:
    def render(self, *args):
        return "ok"
    def __call__(self, *args):
        return "ok"
    def __del__(self):
        assert rust.registry_generation() > before_generation
        reenter()
        finished.append(True)

if kind in ("tag", "block_tag", "assign_tag", "raw_block_tag"):
    register_fn = getattr(rust, "register_" + kind + "_handler")
    register = lambda handler: register_fn(name, "endprobe", handler) if kind in ("block_tag", "raw_block_tag") else register_fn(name, handler)
    remove = lambda: getattr(rust, "unregister_" + kind + "_handler")(name)
    clear = getattr(rust, "clear_" + kind + "_handlers")
    reenter = lambda: getattr(rust, "has_" + kind + "_handler")(name)
elif kind == "filter":
    register = lambda handler: rust.register_custom_filter(name, handler)
    remove = lambda: rust.unregister_custom_filter(name)
    clear = rust.clear_custom_filters
    reenter = lambda: rust.has_custom_filter(name)
elif kind == "loader":
    register = rust.register_library_loader
    clear = rust.clear_library_loader
    reenter = rust.has_library_loader
elif kind == "translator":
    register = rust.register_translator
    clear = rust.clear_translator
    reenter = lambda: rust.register_translator(lambda *args: "ok")
else:
    register_fn = getattr(rust, "register_" + kind + "_scope_hooks")
    register = lambda handler: register_fn(handler, lambda *args: None)
    reenter = lambda: register_fn(lambda *args: None, lambda *args: None)

register(Handler())
before_generation = rust.registry_generation()
if action == "replace":
    class Replacement:
        def render(self, *args): return "ok"
        def __call__(self, *args): return "ok"
    register(Replacement())
elif action == "remove":
    assert remove()
else:
    clear()
assert finished == [True], finished
faulthandler.cancel_dump_traceback_later()
print("finalizer completed", flush=True)
"""


@pytest.mark.parametrize(
    "kind,action",
    [
        (kind, action)
        for kind in ("tag", "block_tag", "assign_tag", "raw_block_tag", "filter")
        for action in ("replace", "remove", "clear")
    ]
    + [(kind, action) for kind in ("loader", "translator") for action in ("replace", "clear")]
    + [("language", "replace"), ("timezone", "replace")],
)
def test_registry_finalizer_can_reenter(kind, action):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    try:
        result = subprocess.run(
            [sys.executable, "-c", _CHILD, kind, action],
            env=env,
            text=True,
            capture_output=True,
            timeout=12,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"{kind}/{action} deadlocked during finalizer reentry:\n{exc.stderr}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "finalizer completed" in result.stdout


def test_registry_churn_and_collection_make_progress():
    """Exercise native reads/writes while GC requests global thread pauses."""
    script = r"""
import faulthandler
import gc
import sys
import sysconfig
import threading
from concurrent.futures import ThreadPoolExecutor
from djust import _rust as rust
faulthandler.dump_traceback_later(10)
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "free-threaded native validation requires GIL off"
start = threading.Barrier(4, timeout=5)
class Handler:
    def render(self, *args): return "ok"
def writer():
    start.wait()
    for _ in range(1000):
        rust.register_tag_handler("churn_3088", Handler())
        rust.unregister_tag_handler("churn_3088")
def reader():
    start.wait()
    for _ in range(2000):
        rust.has_tag_handler("churn_3088")
        rust.get_registered_tags()
def collector():
    start.wait()
    for _ in range(50):
        gc.collect()
with ThreadPoolExecutor(max_workers=4) as pool:
    jobs = [pool.submit(fn) for fn in (writer, writer, reader, collector)]
    for job in jobs:
        job.result(timeout=10)
faulthandler.cancel_dump_traceback_later()
print("churn completed", flush=True)
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"registry churn stalled with concurrent GC:\n{exc.stderr}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "churn completed" in result.stdout


@pytest.mark.parametrize("action", ["replace", "remove", "clear"])
def test_finalizer_compilation_sees_updated_registry(action):
    """A reentrant compile must not accept a stale cached parse/validator."""
    script = r"""
import sys
import sysconfig
from djust import _rust as rust
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "free-threaded native validation requires GIL off"
source = "{% finalizer_generation_3088 %}"
seen = []
class Old:
    def render(self, *args): return "old"
    def __del__(self):
        try:
            rust.compile_template(source)
        except Exception:
            seen.append("rejected")
        else:
            seen.append("accepted stale parse")
class New:
    def render(self, *args): return "new"
    def validate_at_parse(self, *args): raise ValueError("new handler refusal")
rust.register_tag_handler("finalizer_generation_3088", Old())
rust.compile_template(source)
if sys.argv[1] == "replace":
    rust.register_tag_handler("finalizer_generation_3088", New())
elif sys.argv[1] == "remove":
    rust.unregister_tag_handler("finalizer_generation_3088")
else:
    rust.clear_tag_handlers()
assert seen == ["rejected"], seen
"""
    result = subprocess.run(
        [sys.executable, "-c", script, action],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        text=True,
        capture_output=True,
        timeout=12,
    )
    assert result.returncode == 0, result.stdout + result.stderr
