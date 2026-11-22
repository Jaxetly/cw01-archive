resource "yandex_iam_service_account" "gateway" {
  folder_id = var.folder_id
  name      = "${local.name_prefix}gateway-sa"
}

resource "yandex_resourcemanager_folder_iam_member" "gateway_queue_writer" {
  folder_id = var.folder_id
  role      = "ymq.writer"
  member    = "serviceAccount:${yandex_iam_service_account.gateway.id}"
}

resource "yandex_ydb_database_iam_member" "gateway_database_reader" {
  database_id = yandex_ydb_database_serverless.archives.id
  role        = "ydb.viewer"
  member      = "serviceAccount:${yandex_iam_service_account.gateway.id}"
}

resource "yandex_storage_bucket_iam_binding" "gateway_bucket_reader" {
  bucket  = yandex_storage_bucket.archives.bucket
  role    = "storage.viewer"
  members = ["serviceAccount:${yandex_iam_service_account.gateway.id}"]
}

resource "yandex_api_gateway" "archive" {
  folder_id = var.folder_id
  name      = "${local.name_prefix}gateway"

  spec = templatefile("${path.module}/../openapi.yaml", {
    service_account_id = yandex_iam_service_account.gateway.id
    folder_id          = var.folder_id
    queue_url          = yandex_message_queue.tasks.id
    database_path      = yandex_ydb_database_serverless.archives.database_path
    table_name         = terraform_data.archives.output.table_name
    bucket_name        = yandex_storage_bucket.archives.bucket
  })

  depends_on = [
    yandex_resourcemanager_folder_iam_member.gateway_queue_writer,
    yandex_ydb_database_iam_member.gateway_database_reader,
    yandex_storage_bucket_iam_binding.gateway_bucket_reader,
  ]
}

output "gateway_url" {
  description = "Базовый HTTPS-адрес API архивов."
  value       = "https://${yandex_api_gateway.archive.domain}"
}
