"""Create and delete the document table with Terraform's IAM token."""

import argparse
import json
import os
import sys
import time
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


KEY_SCHEMA = [{"AttributeName": "key", "KeyType": "HASH"}]
KEY_ATTRIBUTE = {"AttributeName": "key", "AttributeType": "S"}
NOT_FOUND = "ResourceNotFoundException"
IN_USE = "ResourceInUseException"
RETRYABLE = {"ProvisionedThroughputExceededException", "RequestLimitExceeded", "ThrottlingException"}
KNOWN_ERRORS = RETRYABLE | {
    NOT_FOUND,
    IN_USE,
    "AccessDeniedException",
    "InternalServerError",
    "LimitExceededException",
    "MissingAuthenticationToken",
    "NotAuthorized",
    "ValidationError",
    "ValidationException",
}


class TableError(Exception):
    """An error suitable for display without sensitive request details."""


class APIError(TableError):
    def __init__(self, action, status, code):
        super().__init__(f"{action}: HTTP {status} ({code})")
        self.status = status
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def error_code(response):
    try:
        details = json.load(response)
        code = details.get("__type", details.get("code", ""))
        code = code.split("#")[-1].split(":")[0]
        if code in KNOWN_ERRORS:
            return code
    except (ValueError, AttributeError):
        pass
    return "UnknownError"


class DocumentTable:
    def __init__(self, endpoint, name, token):
        try:
            url = urlsplit(endpoint)
        except ValueError:
            raise TableError("DOCUMENT_API_ENDPOINT must be an HTTPS endpoint") from None
        if url.scheme != "https" or not url.hostname or url.username is not None:
            raise TableError("DOCUMENT_API_ENDPOINT must be an HTTPS endpoint")
        self.endpoint = endpoint
        self.name = name
        self.token = token
        self.opener = build_opener(NoRedirect())
        self.deadline = time.monotonic() + 180

    def request(self, action, **parameters):
        request = Request(
            self.endpoint,
            data=json.dumps({"TableName": self.name, **parameters}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/x-amz-json-1.0",
                "X-Amz-Target": f"DynamoDB_20120810.{action}",
            },
        )
        delay = 1
        while True:
            try:
                with self.opener.open(request, timeout=15) as response:
                    return json.load(response)
            except HTTPError as error:
                with error:
                    failure = APIError(action, error.code, error_code(error))
                if failure.status not in {429, 500, 502, 503, 504} and failure.code not in RETRYABLE:
                    raise failure from None
            except (OSError, HTTPException):
                failure = TableError(f"{action}: could not reach the Document API")
            except ValueError:
                raise TableError(f"{action}: invalid JSON in the Document API response") from None
            if time.monotonic() + delay >= self.deadline:
                raise failure from None
            time.sleep(delay)
            delay = min(delay * 2, 8)

    def describe(self):
        try:
            response = self.request("DescribeTable")
        except APIError as error:
            if error.code == NOT_FOUND:
                return None
            raise

        if not isinstance(response, dict) or not isinstance(response.get("Table"), dict):
            raise TableError("DescribeTable: server did not return a table description")
        return response["Table"]

    def verify_schema(self, table):
        attributes = table.get("AttributeDefinitions")
        if (
            table.get("KeySchema") != KEY_SCHEMA
            or not isinstance(attributes, list)
            or KEY_ATTRIBUTE not in attributes
        ):
            raise TableError(f"Table {self.name} must use the string partition key 'key'")

    def create(self):
        creation_requested = False
        while time.monotonic() < self.deadline:
            table = self.describe()
            if table is not None and table.get("TableStatus") == "ACTIVE":
                self.verify_schema(table)
                print(f"Document table {self.name} is ACTIVE")
                return
            if table is None and not creation_requested:
                try:
                    self.request(
                        "CreateTable",
                        AttributeDefinitions=[KEY_ATTRIBUTE],
                        KeySchema=KEY_SCHEMA,
                        BillingMode="PAY_PER_REQUEST",
                    )
                except APIError as error:
                    if error.code != IN_USE:
                        raise
                creation_requested = True
            time.sleep(2)
        raise TableError(f"Timed out waiting for document table {self.name} to become ACTIVE")

    def delete(self):
        while time.monotonic() < self.deadline:
            table = self.describe()
            if table is None:
                print(f"Document table {self.name} is absent")
                return
            if table.get("TableStatus") == "ACTIVE":
                self.verify_schema(table)
                try:
                    self.request("DeleteTable")
                except APIError as error:
                    if error.code not in {NOT_FOUND, IN_USE}:
                        raise
            time.sleep(2)
        raise TableError(f"Timed out waiting for document table {self.name} to be deleted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "delete"))
    args = parser.parse_args()

    names = ("DOCUMENT_API_ENDPOINT", "DOCUMENT_TABLE_NAME", "YC_TOKEN")
    values = [os.environ.get(name) for name in names]
    for name, value in zip(names, values):
        if not value:
            parser.error(f"missing environment variable {name}")

    try:
        table = DocumentTable(*values)
        if args.action == "create":
            table.create()
        else:
            table.delete()
    except TableError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
