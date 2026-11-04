"""Web pages that jobs make (games, small tools, charts), shared on your home network.

A job runs in a sandbox that can't take connections, and stops when it's done, so Alfred
serves its pages himself: everything in the workspace's www/ folder, read-only, at
http://<this computer>.local:<ALFRED_WEB_PORT>/ for as long as he runs. Off unless
ALFRED_WEB_PORT is set. Only www/ is shared: a link (symlink) pointing outside it is
refused, so notes, the database and everything else stay private. Anyone on the same
Wi-Fi can open the pages, so keep it for home use.
"""

import socket
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from loguru import logger

WWW = "www"  # in the workspace, where jobs put pages to share


class SharedFolder(SimpleHTTPRequestHandler):
    """Serves files from one folder, and nothing it links to outside it."""

    def translate_path(self, path: str) -> str:
        root = Path(self.directory).resolve()
        real = Path(super().translate_path(path)).resolve()  # follows links to where they lead
        if real != root and root not in real.parents:
            return str(root / ".not-shared")  # doesn't exist: "404 not found"
        return str(real)

    def log_message(self, format: str, *args) -> None:
        logger.debug(f"Web: {self.address_string()} {format % args}")


class WebShare:
    def __init__(self, folder: Path, port: int):
        folder.mkdir(parents=True, exist_ok=True)
        handler = partial(SharedFolder, directory=str(folder))
        self._server = ThreadingHTTPServer(("0.0.0.0", port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="web", daemon=True)
        self.address = address(port)
        self.ip_address = f"http://{lan_ip()}:{self.port}/"

    def start(self) -> None:
        self._thread.start()
        logger.info(f"Sharing web pages at {self.address} (also {self.ip_address})")

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def address_text(self) -> str:
        """For Alfred's instructions, so "what's your address?" gets a real answer (jobs
        can't tell: their sandbox has its own network)."""
        return (
            f"\n\n## Your web address\nWeb pages and games you make are shared on the "
            f"user's home network at {self.address} (or {self.ip_address}). If asked for your "
            "address or IP, give these yourself; a background job can't find them."
        )

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def start_web(workspace: Path, port: int | None) -> WebShare | None:
    """Start sharing workspace/www/ if a port is set. Never stops Alfred from starting."""
    if port is None:
        return None
    try:
        web = WebShare(workspace / WWW, port)
    except OSError as error:  # e.g. the port is already in use
        logger.warning(f"Couldn't share web pages on port {port}: {error}")
        return None
    web.start()
    return web


def job_text(port: int) -> str:
    """For jobs' instructions: where to put pages, and the address to give the user."""
    return (
        "\n\n## Web pages the user can open\n"
        f"Pages the user should open in a browser (games, small tools, charts, explainers) go in "
        f"`{WWW}/<short-name>/index.html` in the current folder, as one self-contained file: "
        "HTML, CSS and JavaScript all inside it, nothing to install or run to view it. If a "
        "framework really helps, build it into plain files in that folder. Never put secrets "
        "or keys in a page. Check the code for errors before you finish. The user opens it at "
        f"{address(port)}<short-name>/ from any device on their home network: end your result "
        "with that address."
    )


def address(port: int) -> str:
    """The name other devices on the network can use, e.g. http://raspy.local:8080/."""
    name = socket.gethostname()
    if not name.endswith(".local"):
        name += ".local"
    return f"http://{name}:{port}/"


def lan_ip() -> str:
    """This computer's address on the home network (no data is actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.168.0.1", 9))
            return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
