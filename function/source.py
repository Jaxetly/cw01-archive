"""Загрузка файла по публичному HTTP(S)-адресу."""

import ipaddress
import socket
import ssl
import time
from contextlib import closing
from http.client import HTTPConnection, HTTPException
from urllib.parse import quote, urljoin, urlsplit


MAX_SOURCE_BYTES = 32 * 1024 * 1024
DOWNLOAD_TIMEOUT = 90
SOCKET_TIMEOUT = 15
MAX_REDIRECTS = 5
REDIRECT_STATUS = {301, 302, 303, 307, 308}
GLOBAL_IPV6 = ipaddress.ip_network("2000::/3")


class SourceError(RuntimeError):
    """Ошибка источника, которую можно показать в логах функции."""


def source_url(url):
    if not isinstance(url, str) or not 1 <= len(url) <= 2048:
        raise SourceError("URL источника должен быть строкой длиной до 2048 символов.")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise SourceError("URL источника содержит недопустимые символы.")
    try:
        url.encode("utf-8")
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise SourceError("Некорректный URL источника.") from None
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or port == 0
    ):
        raise SourceError("Нужен HTTP(S)-адрес источника без учётных данных.")
    return parts


def public_ip(host):
    address = ipaddress.ip_address(host)
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return public_ip(address.ipv4_mapped)
        if address not in GLOBAL_IPV6 or address.sixtofour or address.teredo:
            return False
    return address.is_global and not address.is_multicast and not address.is_reserved


def timeout_until(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SourceError("Истекло время загрузки файла.")
    return min(SOCKET_TIMEOUT, remaining)


class SourceConnection(HTTPConnection):
    def __init__(self, parts, deadline):
        self.secure = parts.scheme == "https"
        self.default_port = 443 if self.secure else 80
        self.deadline = deadline
        super().__init__(parts.hostname, parts.port, timeout=timeout_until(deadline))

    def connect(self):
        host = self.host.encode("idna").decode("ascii")
        addresses = socket.getaddrinfo(host, self.port, type=socket.SOCK_STREAM)
        if not addresses or any(not public_ip(item[4][0]) for item in addresses):
            raise SourceError("Источник должен разрешаться в публичные IP-адреса.")

        # Подключаемся к проверенному адресу напрямую, сохраняя исходный хост для TLS.
        for family, kind, protocol, _, address in addresses:
            try:
                connection = socket.socket(family, kind, protocol)
            except OSError:
                continue
            try:
                connection.settimeout(timeout_until(self.deadline))
                connection.connect(address)
                if self.secure:
                    connection.settimeout(timeout_until(self.deadline))
                    connection = ssl.create_default_context().wrap_socket(connection, server_hostname=host)
            except OSError:
                connection.close()
            except SourceError:
                connection.close()
                raise
            else:
                self.sock = connection
                return
        raise SourceError("Не удалось подключиться к источнику.")


def request_target(parts):
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        return path + "?" + quote(parts.query, safe="/?%:@!$&'()*+,;=-._~")
    return path


def read_source(response, connection, deadline):
    if response.getheader("Content-Encoding", "identity").lower() != "identity":
        raise SourceError("Источник использовал неподдерживаемое кодирование.")
    length_header = response.getheader("Content-Length")
    length = int(length_header) if length_header is not None else None
    if length is not None and not 0 <= length <= MAX_SOURCE_BYTES:
        raise SourceError("Размер исходного файла превышает 32 МиБ.")

    content = bytearray()
    while len(content) <= MAX_SOURCE_BYTES:
        connection.settimeout(timeout_until(deadline))
        block = response.read1(min(65536, MAX_SOURCE_BYTES + 1 - len(content)))
        if not block:
            if length is not None and len(content) != length:
                raise SourceError("Файл получен не полностью.")
            return content
        content.extend(block)
    raise SourceError("Размер исходного файла превышает 32 МиБ.")


def download(url):
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT
    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        parts = source_url(current_url)
        with closing(SourceConnection(parts, deadline)) as connection:
            try:
                connection.request("GET", request_target(parts), headers={"Accept-Encoding": "identity"})
                response_socket = connection.sock
                response_socket.settimeout(timeout_until(deadline))
                with connection.getresponse() as response:
                    if response.status == 200:
                        return read_source(response, response_socket, deadline)
                    if response.status not in REDIRECT_STATUS:
                        raise SourceError(f"Источник вернул HTTP {response.status}.")
                    location = response.getheader("Location")
                    if not location:
                        raise SourceError("Источник вернул перенаправление без адреса.")
                    current_url = urljoin(current_url, location)
            except (HTTPException, OSError, ValueError):
                raise SourceError("Не удалось загрузить файл по URL источника.") from None
    raise SourceError("Источник превысил допустимое число перенаправлений.")
