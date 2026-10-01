"""The display process's control socket: web -> display commands with acks.

- :mod:`src.ipc.contract` -- the versioned messages, the framing and where
  the socket lives. Shared by both sides; standard library only.
- :mod:`src.ipc.server` -- the display side: a threaded Unix-socket server
  whose handlers only queue work for the render thread.
- :mod:`src.ipc.client` -- the web side: one short-timeout request.

See docs/IPC_CONTROL_SOCKET.md for the protocol, the security model and the
stage plan.
"""
