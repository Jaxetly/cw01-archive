#!/usr/bin/env python3
"""Проверка нового бакета контрольной перед его удалением Terraform."""

import argparse
import http.client
import io
import os
import re
import sys
import uuid
import zipfile


HOST = "storage.yandexcloud.net"
TIMEOUT_SECONDS = 15
ENTRY_NAME = "check.txt"
ENTRY_CONTENT = b"Archive storage check\n"


class CheckError(Exception):
    pass


def storage_request(method, path, expected_status, *, token=None, data=None, limit=0):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/zip"

    operation = f"{method} ({'с IAM-токеном' if token else 'без авторизации'})"
    connection = http.client.HTTPSConnection(HOST, timeout=TIMEOUT_SECONDS)
    try:
        connection.request(method, path, body=data, headers=headers)
        with connection.getresponse() as response:
            if response.status != expected_status:
                raise CheckError(
                    f"{operation}: ожидался HTTP {expected_status}, получен {response.status}"
                )
            return response.read(limit) if limit else b""
    except (OSError, ValueError, http.client.HTTPException):
        raise CheckError(f"{operation}: не удалось обменяться данными с Object Storage") from None
    finally:
        connection.close()


def check_bucket(bucket_name, token):
    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(ENTRY_NAME, ENTRY_CONTENT)
        expected_zip = buffer.getvalue()

    # Уникальный тестовый объект не затрагивает архивы приложения.
    object_key = f"infra-check-{uuid.uuid4()}.zip"
    path = f"/{bucket_name}/{object_key}"
    try:
        storage_request("PUT", path, 200, token=token, data=expected_zip)
        downloaded_zip = storage_request("GET", path, 200, token=token, limit=len(expected_zip) + 1)
        if downloaded_zip != expected_zip:
            raise CheckError("Скачанный ZIP отличается от загруженного файла")
        with zipfile.ZipFile(io.BytesIO(downloaded_zip)) as archive:
            if archive.namelist() != [ENTRY_NAME] or archive.read(ENTRY_NAME) != ENTRY_CONTENT:
                raise CheckError("Содержимое скачанного ZIP не совпадает с исходным")
        storage_request("GET", path, 403)
    except CheckError:
        # Даже если ответ на PUT потерян, удаление затрагивает только наш новый ключ.
        try:
            storage_request("DELETE", path, 204, token=token)
        except CheckError as cleanup_error:
            print(f"Не удалось удалить тестовый объект: {cleanup_error}", file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bucket_name", help="имя нового тестового бакета vvotNN-…")
    args = parser.parse_args()
    if not re.fullmatch(r"vvot[0-9]{2}-[a-z0-9](?:[a-z0-9.-]{0,54}[a-z0-9])?", args.bucket_name):
        parser.error("ожидалось имя бакета с префиксом vvotNN-")

    token = os.environ.get("YC_TOKEN", "")
    if not token or not token.isascii() or any(char.isspace() for char in token):
        parser.error("задайте IAM-токен в переменной окружения YC_TOKEN")

    try:
        check_bucket(args.bucket_name, token)
    except CheckError as error:
        print(error, file=sys.stderr)
        return 1

    print("ZIP загружен и скачан через IAM, содержимое совпадает. Анонимный GET вернул 403.")
    print("Тестовый объект оставлен в бакете для проверки terraform destroy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
