"""Проверка HTTP API системы архивирования от имени клиента."""

import argparse
import io
import json
import sys
import time
import uuid
import zipfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


SOURCE_URL = "https://httpbingo.org/base64/Y2xvdWQtYXJjaGl2ZS1jaGVjawo="
MISSING_KEY = "00000000-0000-7000-8000-000000000000.zip"


class CheckError(Exception):
    pass


def request(url, expected_status, *, body=None, label):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=25) as response:
            status = response.status
            content_type = response.headers.get_content_type()
            payload = response.read(2_500_001)
    except HTTPError as error:
        with error:
            status = error.code
        content_type, payload = "", b""
    except (URLError, OSError):
        raise CheckError(f"{label}: ошибка соединения") from None

    if status != expected_status:
        raise CheckError(f"{label}: ожидался HTTP {expected_status}, получен {status}")
    if len(payload) > 2_500_000:
        raise CheckError(f"{label}: превышен максимальный размер ответа")
    return content_type, payload


def get_json(url, label):
    content_type, payload = request(url, 200, label=label)
    if content_type != "application/json":
        raise CheckError(f"{label}: ожидался JSON")
    try:
        return json.loads(payload)
    except ValueError:
        raise CheckError(f"{label}: получен некорректный JSON") from None


def wait_for_archive(archives_url, task):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        records = get_json(archives_url, "GET /archives")
        if not isinstance(records, list):
            raise CheckError("GET /archives: ожидался массив")
        for record in records:
            if isinstance(record, dict) and record.get("name") == task["name"]:
                return record
        time.sleep(2)
    raise CheckError("Запись об архиве не появилась в списке за 120 секунд")


def verify_key(key):
    if not isinstance(key, str) or not key.endswith(".zip"):
        raise CheckError("Ожидался ключ UUIDv7 с расширением .zip")
    try:
        value = uuid.UUID(key[:-4])
    except ValueError:
        raise CheckError("Некорректный UUID в ключе архива") from None
    if value.version != 7 or key != f"{value}.zip":
        raise CheckError("Ожидался ключ UUIDv7 с расширением .zip")


def check_api(base_url, source_url):
    archives_url = f"{base_url}/archives"
    tasks_url = f"{base_url}/tasks"
    _, original = request(source_url, 200, label="Скачивание исходного файла")

    request(tasks_url, 400, body={"name": "check.txt"}, label="POST /tasks без URL")
    request(tasks_url, 400, body={"name": "check.txt", "url": "file:///check.txt"}, label="POST /tasks со схемой file")
    request(f"{archives_url}/{MISSING_KEY}", 404, label="GET /archives/{key} для отсутствующей записи")
    request(f"{base_url}/download/{MISSING_KEY}", 404, label="GET /download/{key} для отсутствующего объекта")
    print("Некорректное задание вернуло 400, отсутствующая запись и ZIP вернули 404.", flush=True)

    task = {"name": f"check-{uuid.uuid4().hex}.txt", "url": source_url}
    request(tasks_url, 202, body=task, label="POST /tasks")
    print("Задание принято с HTTP 202. Жду запись в YDB.", flush=True)
    record = wait_for_archive(archives_url, task)
    key = record.get("key")
    verify_key(key)
    expected = {**task, "key": key}
    encoded_key = quote(key, safe="")
    if record != expected or get_json(f"{archives_url}/{encoded_key}", "GET /archives/{key}") != expected:
        raise CheckError("Метаданные архива не совпали с исходным заданием")

    content_type, payload = request(f"{base_url}/download/{encoded_key}", 200, label="GET /download/{key}")
    if content_type != "application/zip":
        raise CheckError("GET /download/{key}: ожидался Content-Type application/zip")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if archive.namelist() != [task["name"]] or archive.read(task["name"]) != original:
                raise CheckError("Содержимое скачанного ZIP отличается от исходного файла")
    except (OSError, RuntimeError, zipfile.BadZipFile):
        raise CheckError("Полученный ZIP не удалось открыть") from None
    print(f"Проверены список, карточка и ZIP {key}. Содержимое совпало с исходником.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gateway_url", help="значение Terraform output gateway_url")
    parser.add_argument("--source-url", default=SOURCE_URL, help="публичный URL небольшого неизменяемого файла")
    args = parser.parse_args()
    try:
        check_api(args.gateway_url.rstrip("/"), args.source_url)
    except CheckError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
