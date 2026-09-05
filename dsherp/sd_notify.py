"""systemd readiness and watchdog pings; a no-op anywhere else.

A worker that stops ticking is worse than one that exits: the queue keeps filling
and nothing restarts it. Under systemd the watchdog turns a hang into a restart.
"""
import os
import socket


def _address():
    address = os.environ.get('NOTIFY_SOCKET')
    if not address:
        return None
    # An abstract namespace socket is spelled with a leading '@' by systemd.
    return '\0' + address[1:] if address.startswith('@') else address


def notify(state):
    """Best effort: supervision must never be able to fail a business run."""
    address = _address()
    if not address:
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as sock:
            sock.connect(address)
            sock.sendall(state.encode())
        return True
    except OSError:
        return False


def ready():
    return notify('READY=1')


def watchdog():
    return notify('WATCHDOG=1')
