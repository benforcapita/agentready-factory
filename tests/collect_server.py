"""Offline collection server and reproducible sample-bundle generator.

Run: python -m tests.collect_server fixtures/collect/sample
"""

from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
from socketserver import BaseRequestHandler, ThreadingTCPServer
import sys
from threading import Thread
from time import sleep

from arf.collect import collect
from arf.collect.transport import GuardPolicy


def resolve(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]


def local_policy():
    return GuardPolicy({host: ("127.0.0.1/32",) for host in ("shop.example", "www.shop.example")})


@contextmanager
def closing_server():
    class Handler(BaseRequestHandler):
        def handle(self):
            try:
                self.request.recv(1)
            except OSError:
                pass

    class Server(ThreadingTCPServer):
        daemon_threads = True

        def get_request(self):
            request = super().get_request()
            self.accepted += 1
            return request

    instance = Server(("127.0.0.1", 0), Handler)
    instance.accepted = 0
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


@contextmanager
def server():
    class Handler(BaseHTTPRequestHandler):
        server_version = "CollectFixture/1.0"
        sys_version = ""

        def version_string(self):
            return self.server_version

        def do_GET(self):
            host = self.headers["Host"].split(":")[0]
            self.server.hits[host] += 1
            self.server.paths.append(self.path)
            self.server.headers_seen.append(dict(self.headers))
            body = b"fixture body\n"
            redirect = self.server.redirect if self.path == "/llms-full.txt" else None
            redirect = self.server.redirects.get((host, self.path), redirect)
            page = self.server.pages.get((host, self.path))
            if page:
                body = page
            self.send_response(301 if redirect else 200)
            if redirect:
                self.send_header("Location", redirect)
            if page and self.server.content_language:
                self.send_header("Content-Language", self.server.content_language)
            self.send_header("CF-Ray", "fixture-ray")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                if self.server.trickle and self.path == "/robots.txt":
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        sleep(self.server.trickle)
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    instance.hits = Counter()
    instance.paths = []
    instance.headers_seen = []
    instance.redirect = None
    instance.redirects = {}
    instance.pages = {}
    instance.content_language = None
    instance.trickle = 0
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def sample(out):
    with server() as instance:
        return collect("https://shop.example", Path(out), resolver=resolve,
                       scheme="http", port=instance.server_port, policy=local_policy())


if __name__ == "__main__":
    sample(sys.argv[1])
