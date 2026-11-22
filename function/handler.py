"""Обработчик заданий на архивирование из Yandex Message Queue."""

import hashlib
import io
import json
import os
import time
import uuid
import zipfile
from http.client import HTTPException, HTTPSConnection
from urllib.parse import urlsplit

from source import download, request_target, source_url


STORAGE_ENDPOINT = "https://storage.yandexcloud.net"
MAX_ARCHIVE_BYTES = 2_500_000
RETRY_STATUS = {429, 500, 502, 503, 504}


class ArchiveError(RuntimeError):
    """Ошибка задания без учётных данных и исходного URL в сообщении."""


def read_task(event):
    try:
        messages = event["messages"]
        if not isinstance(messages, list) or len(messages) != 1:
            raise ArchiveError("Функция обрабатывает ровно одно сообщение.")
        message = messages[0]["details"]["message"]
        task = json.loads(message["body"])
        name, url = task["name"], task["url"]
    except (KeyError, TypeError, ValueError):
        raise ArchiveError("Сообщение не содержит корректного задания.") from None

    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 255
        or name.strip() in {".", ".."}
        or any(char in "/\\:" or ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise ArchiveError("Укажите безопасное имя одного файла без пути.")
    try:
        name.encode("utf-8")
    except UnicodeError:
        raise ArchiveError("Имя файла должно быть в UTF-8.") from None
    source_url(url)
    return message, name, url


def archive_key(message):
    try:
        timestamp = int(message["attributes"]["SentTimestamp"])
        message_id = message["message_id"]
        if not isinstance(message_id, str) or not message_id or not 0 <= timestamp < (1 << 48):
            raise ValueError
        entropy = int.from_bytes(hashlib.sha256(message_id.encode("utf-8")).digest(), "big")
    except (KeyError, TypeError, ValueError):
        return f"{uuid.uuid7()}.zip"

    # UUIDv7 хранит время в первых 48 битах. Остальные биты идентичности
    # выводим из ID сообщения, чтобы повторная доставка сохраняла тот же ключ.
    random_a = (entropy >> 62) & 0xFFF
    random_b = entropy & ((1 << 62) - 1)
    value = (timestamp << 80) | (7 << 76) | (random_a << 64) | (2 << 62) | random_b
    return f"{uuid.UUID(int=value)}.zip"


def create_archive(name, content):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, content)
    if buffer.tell() > MAX_ARCHIVE_BYTES:
        raise ArchiveError("ZIP превышает предельный размер ответа API Gateway (2,5 МБ).")
    return buffer.getvalue()


def cloud_request(service, method, url, token, headers, body):
    try:
        endpoint = urlsplit(url)
        if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username is not None:
            raise ValueError
        host, port, target = endpoint.hostname, endpoint.port, request_target(endpoint)
    except ValueError:
        raise ArchiveError(f"{service}: некорректный HTTPS-адрес.") from None

    request_headers = {**headers, "Authorization": f"Bearer {token}"}
    for pause in (1, 2, 0):
        connection = HTTPSConnection(host, port, timeout=15)
        try:
            connection.request(method, target, body=body, headers=request_headers)
            with connection.getresponse() as response:
                if response.status == 200:
                    return
                error = ArchiveError(f"{service}: HTTP {response.status}.")
                if response.status not in RETRY_STATUS:
                    raise error
        except (HTTPException, OSError, ValueError):
            error = ArchiveError(f"{service}: ошибка сети.")
        finally:
            connection.close()
        if not pause:
            raise error
        time.sleep(pause)


def handler(event, context):
    message, name, url = read_task(event)
    try:
        bucket = os.environ["BUCKET_NAME"]
        ydb_endpoint = os.environ["DOCUMENT_API_ENDPOINT"]
        table = os.environ["ARCHIVES_TABLE"]
        token = context.token["access_token"]
    except (AttributeError, KeyError, TypeError):
        raise ArchiveError("Функции нужны настройки YDB, бакета и токен сервисного аккаунта.") from None
    if not isinstance(token, str) or not token or not token.isascii() or any(char.isspace() for char in token):
        raise ArchiveError("Функция не получила IAM-токен сервисного аккаунта.")

    key = archive_key(message)
    zipped = create_archive(name, download(url))
    cloud_request(
        "Object Storage",
        "PUT",
        f"{STORAGE_ENDPOINT}/{bucket}/{key}",
        token,
        {"Content-Type": "application/zip"},
        zipped,
    )
    # Список архивов получает только те записи, которым уже соответствует ZIP в бакете.
    item = {"name": {"S": name}, "url": {"S": url}, "key": {"S": key}}
    cloud_request(
        "YDB Document API",
        "POST",
        ydb_endpoint,
        token,
        {
            "Content-Type": "application/x-amz-json-1.0",
            "X-Amz-Target": "DynamoDB_20120810.PutItem",
        },
        json.dumps({"TableName": table, "Item": item}, ensure_ascii=False).encode("utf-8"),
    )
    return {"key": key}
