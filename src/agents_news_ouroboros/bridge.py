"""HTTP-мост для Ouroboros: локальный http:// -> https-шлюз с self-signed сертификатом.

Runtime Ouroboros строит httpx-клиент с trust_env=False, поэтому ни
SSL_CERT_FILE, ни системное хранилище на него не действуют, а nginx перед
шлюзом маршрутизирует по заголовку Host. Мост принимает запросы на
127.0.0.1:PORT, подставляет Host шлюза и проверяет его сертификат по
указанному PEM. Ответ отдаётся целиком после получения.
"""

import argparse
import logging
import ssl
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "host", "content-length"}


def make_handler(upstream: str, ca_file: str | None) -> type[BaseHTTPRequestHandler]:
    """Собрать обработчик, пересылающий любой метод на upstream."""
    host = urlsplit(upstream).netloc
    context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _forward(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
            headers["Host"] = host
            request = urllib.request.Request(
                upstream + self.path, data=body, headers=headers, method=self.command,
            )
            try:
                with urllib.request.urlopen(request, context=context, timeout=600) as response:
                    self._reply(response.status, response.headers, response.read())
            except urllib.error.HTTPError as exc:
                self._reply(exc.code, exc.headers, exc.read())
            except (urllib.error.URLError, OSError) as exc:
                logger.error("Шлюз недоступен: %s", exc)
                self._reply(502, {}, str(exc).encode())

        def _reply(self, status: int, headers, payload: bytes) -> None:
            self.send_response(status)
            for key, value in headers.items():
                if key.lower() not in HOP_BY_HOP:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _forward

        def log_message(self, fmt: str, *args) -> None:
            logger.debug("%s " + fmt, self.client_address[0], *args)

    return Handler


def serve(listen: str, upstream: str, ca_file: str | None) -> ThreadingHTTPServer:
    """Поднять мост; вернуть сервер (вызывающий сам делает serve_forever)."""
    host, _, port = listen.rpartition(":")
    server = ThreadingHTTPServer((host or "127.0.0.1", int(port)), make_handler(upstream, ca_file))
    logger.info("Мост http://%s:%d -> %s", *server.server_address[:2], upstream)
    return server


def main() -> int:
    """agents-news-ouroboros-bridge --upstream https://... [--listen 127.0.0.1:4000] [--ca-file PEM]."""
    parser = argparse.ArgumentParser(description="HTTP-мост к https-шлюзу для Ouroboros")
    parser.add_argument("--upstream", required=True, help="адрес шлюза, например https://litellm.home.arpa")
    parser.add_argument("--listen", default="127.0.0.1:4000")
    parser.add_argument("--ca-file", default=None, help="PEM self-signed сертификата шлюза")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    server = serve(args.listen, args.upstream.rstrip("/"), args.ca_file)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
