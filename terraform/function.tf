locals {
  worker_package_hash = sha256(join(":", [
    filesha256("${path.module}/../function/handler.py"),
    filesha256("${path.module}/../function/source.py"),
    filesha256("${path.module}/../scripts/package_function.py"),
  ]))
  worker_zip = "${path.module}/.terraform/${local.name_prefix}worker-${substr(local.worker_package_hash, 0, 12)}.zip"
}

resource "terraform_data" "worker_package" {
  input            = local.worker_zip
  triggers_replace = [local.worker_package_hash, abspath(path.module)]

  provisioner "local-exec" {
    command = "python3 ../scripts/package_function.py"
    environment = {
      FUNCTION_ZIP = local.worker_zip
    }
  }
}

resource "yandex_iam_service_account" "worker" {
  folder_id = var.folder_id
  name      = "${local.name_prefix}worker"
}

resource "yandex_storage_bucket_iam_binding" "worker_upload" {
  bucket  = yandex_storage_bucket.archives.bucket
  role    = "storage.uploader"
  members = ["serviceAccount:${yandex_iam_service_account.worker.id}"]
}

resource "yandex_ydb_database_iam_member" "worker_write" {
  database_id = yandex_ydb_database_serverless.archives.id
  role        = "ydb.editor"
  member      = "serviceAccount:${yandex_iam_service_account.worker.id}"
}

resource "yandex_function" "archive" {
  folder_id          = var.folder_id
  name               = "${local.name_prefix}archive"
  runtime            = "python314"
  entrypoint         = "handler.handler"
  memory             = 256
  execution_timeout  = "600"
  service_account_id = yandex_iam_service_account.worker.id
  user_hash          = local.worker_package_hash

  environment = {
    BUCKET_NAME           = yandex_storage_bucket.archives.bucket
    DOCUMENT_API_ENDPOINT = terraform_data.archives.output.endpoint
    ARCHIVES_TABLE        = terraform_data.archives.output.table_name
  }

  content {
    zip_filename = terraform_data.worker_package.output
  }

  depends_on = [
    yandex_storage_bucket_iam_binding.worker_upload,
    yandex_ydb_database_iam_member.worker_write,
  ]
}

resource "yandex_iam_service_account" "trigger" {
  folder_id = var.folder_id
  name      = "${local.name_prefix}trigger"
}

resource "yandex_resourcemanager_folder_iam_member" "trigger_read" {
  folder_id = var.folder_id
  role      = "ymq.reader"
  member    = "serviceAccount:${yandex_iam_service_account.trigger.id}"
}

resource "yandex_function_iam_member" "trigger_invoke" {
  function_id = yandex_function.archive.id
  role        = "functions.functionInvoker"
  member      = "serviceAccount:${yandex_iam_service_account.trigger.id}"
}

resource "yandex_function_trigger" "tasks" {
  folder_id = var.folder_id
  name      = "${local.name_prefix}tasks-trigger"

  message_queue {
    queue_id           = yandex_message_queue.tasks.arn
    service_account_id = yandex_iam_service_account.trigger.id
    batch_size         = "1"
    batch_cutoff       = "0"
  }

  function {
    id                 = yandex_function.archive.id
    service_account_id = yandex_iam_service_account.trigger.id
    tag                = "$latest"
  }

  depends_on = [
    yandex_resourcemanager_folder_iam_member.trigger_read,
    yandex_function_iam_member.trigger_invoke,
  ]
}
