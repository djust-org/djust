"""How the harness decides its private redis-server is ready (#4136).

The first runner run of the Redis rows died with ``hang guard: no '' line from
redis-server`` after 120 s and nothing else: no return code, no output, no
argv. The harness now detects readiness by a PING that answers (not by parsing
a log line), reports a process that exits at once instead of waiting out the
guard, carries the return code, both output streams and the argv in every
failure, keeps the socket path under the kernel's limit, and falls back to TCP
when the Unix socket never answers. These tests drive ``redis_server`` with
small fake servers, so they need no Redis.
"""

from __future__ import annotations

import os
import shutil
import socket
import stat
import sys
from pathlib import Path

import pytest

from tests.integration.multinode import harness
from tests.integration.multinode.conftest import redis_transport

# A fake redis-server: reads the same argv the harness passes, and answers +PONG
# on whichever endpoint MODE says. It never prints a "Ready" line, so a harness
# that parsed the log would not see it ready.
FAKE = r"""
import os, socket, sys, time
args = sys.argv[1:]
opts = {args[i]: args[i + 1] for i in range(0, len(args) - 1, 2) if args[i].startswith("--")}
mode = os.environ["FAKE_REDIS_MODE"]
if mode == "exit":
    sys.stdout.write("fake-out line\n")
    sys.stderr.write("fake-err line\n")
    sys.exit(3)
if mode == "hang":
    time.sleep(600)
port = int(opts["--port"])
if mode == "tcp_only" and port == 0:
    time.sleep(600)  # alive, silent, and no Unix socket: confinement as it looks
if port:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
else:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(opts["--unixsocket"])
server.listen(16)
while True:
    conn, _ = server.accept()
    conn.recv(64)
    conn.sendall(b"+PONG\r\n")
    conn.close()
"""


@pytest.fixture
def fake_redis(tmp_path, monkeypatch):
    script = tmp_path / "fake-redis-server"
    script.write_text("#!%s\n%s" % (sys.executable, FAKE))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    def use(mode: str) -> str:
        monkeypatch.setenv("FAKE_REDIS_MODE", mode)
        return str(script)

    return use


def test_ready_by_ping_on_a_unix_socket_without_any_log_line(fake_redis):
    with harness.redis_server(fake_redis("unix"), guard_s=30) as url:
        assert redis_transport(url) == "unix"
        path = url[len("unix://") :]
        assert harness._redis_pong(socket.AF_UNIX, path)
    assert not os.path.exists(path), "the socket directory outlives the server"


def test_falls_back_to_tcp_when_the_unix_socket_never_answers(fake_redis):
    # The Unix attempt waits its guard (1 s here), then TCP answers at once.
    with harness.redis_server(fake_redis("tcp_only"), guard_s=1) as url:
        assert url.startswith("redis://127.0.0.1:"), url
        assert redis_transport(url) == "tcp"
        port = int(url.rsplit(":", 1)[1].split("/")[0])
        assert harness._redis_pong(socket.AF_INET, ("127.0.0.1", port))


def test_a_process_that_exits_fails_at_once_with_everything_it_said(fake_redis):
    with pytest.raises(harness.RedisStartError) as raised:
        with harness.redis_server(fake_redis("exit"), guard_s=600):
            pass
    # Not the 600 s guard (twice, for both transports): the exit is seen at once.
    # The message proves it: only a seen exit reports "exited before it answered
    # PING" with the return code; a guard that ran out says something else. No
    # wall-clock bound (#3711): a wait for the guard would hit the run's hang guard.
    message = str(raised.value)
    assert "exited before it answered PING" in message
    assert "returncode: 3" in message
    assert "fake-out line" in message
    assert "fake-err line" in message
    assert "--unixsocket" in message  # the argv of the Unix attempt
    assert "'--bind', '127.0.0.1'" in message  # and of the TCP attempt
    assert "neither a Unix socket nor TCP" in message


def test_a_silent_live_process_fails_after_the_guard_naming_both_attempts(fake_redis):
    with pytest.raises(harness.RedisStartError) as raised:
        with harness.redis_server(fake_redis("hang"), guard_s=0.5):
            pass
    message = str(raised.value)
    assert message.count("did not answer PING within 0.5s") == 2
    assert "returncode: None" in message
    assert "stdout:\n    <empty>" in message


def test_the_argv_pins_logging_to_stdout_and_the_foreground(tmp_path):
    argv = harness._redis_argv("redis-server", str(tmp_path))
    assert argv[argv.index("--logfile") + 1] == ""
    assert argv[argv.index("--daemonize") + 1] == "no"
    assert argv[argv.index("--save") + 1] == ""


def test_a_socket_path_over_the_limit_moves_under_tmp(tmp_path, monkeypatch):
    long_dir = tmp_path / ("d" * 120)
    created = []

    def long_tempdir(prefix: str) -> str:
        long_dir.mkdir()
        created.append(long_dir)
        return str(long_dir)

    monkeypatch.setattr(harness, "_short_tempdir", long_tempdir)
    directory = harness._redis_socket_dir()
    try:
        assert created, "the long candidate was never tried"
        assert not long_dir.exists(), "the rejected long directory was left behind"
        assert directory.startswith("/tmp/"), directory
        path = os.path.join(directory, "r.sock")
        assert len(path.encode()) <= harness.MAX_UNIX_SOCKET_PATH
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_a_short_socket_path_is_kept(monkeypatch, tmp_path):
    short = Path("/tmp") / ("mnr-keep-%d" % os.getpid())
    monkeypatch.setattr(harness, "_short_tempdir", lambda prefix: (short.mkdir(), str(short))[1])
    try:
        assert harness._redis_socket_dir() == str(short)
    finally:
        shutil.rmtree(short, ignore_errors=True)


@pytest.mark.skipif(shutil.which("redis-server") is None, reason="redis-server is not installed")
def test_a_real_redis_server_answers_on_its_unix_socket():
    with harness.redis_server() as url:
        assert redis_transport(url) == "unix"
        assert harness._redis_pong(socket.AF_UNIX, url[len("unix://") :])
