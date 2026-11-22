# The Document API accepts the same IAM token as the Yandex provider.
resource "terraform_data" "archives" {
  input = {
    endpoint   = yandex_ydb_database_serverless.archives.document_api_endpoint
    table_name = "archives"
  }
  triggers_replace = yandex_ydb_database_serverless.archives.id

  provisioner "local-exec" {
    command = "python3 ../scripts/document_table.py create"
    environment = {
      DOCUMENT_API_ENDPOINT = self.input.endpoint
      DOCUMENT_TABLE_NAME   = self.input.table_name
    }
  }

  # The endpoint reference makes Terraform delete the table before the database.
  provisioner "local-exec" {
    when    = destroy
    command = "python3 ../scripts/document_table.py delete"
    environment = {
      DOCUMENT_API_ENDPOINT = self.input.endpoint
      DOCUMENT_TABLE_NAME   = self.input.table_name
    }
  }
}
