"""Native Tkinter front-end (prototype).

This package is a proof-of-concept for replacing the WebKitGTK + FastAPI +
uvicorn stack with a native Tk window that calls the existing service layer
directly, in-process. Nothing here imports a web framework or opens a socket.

Only the new-request builder is implemented so the responsiveness of Tk on
the Pi 400 can be felt before committing to a full port.
"""
