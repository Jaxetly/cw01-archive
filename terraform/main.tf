terraform {
  required_version = ">= 1.6.0, < 2.0"

  required_providers {
    yandex = {
      source  = "yandex-cloud/yandex"
      version = "0.238.0"
    }
  }
}

provider "yandex" {
  cloud_id  = var.cloud_id
  folder_id = var.folder_id
}

locals {
  # Номер студента берём из имени бакета. Хеш различает стенды одного студента.
  name_prefix = "${substr(var.bucket_name, 0, 7)}cw01-${substr(sha256(var.bucket_name), 0, 8)}-"
}

resource "yandex_storage_bucket" "archives" {
  bucket    = var.bucket_name
  folder_id = var.folder_id
  max_size  = 10 * 1024 * 1024 * 1024

  # Бакет создаётся специально для этой системы. При destroy удаляются и его архивы.
  force_destroy = true

  anonymous_access_flags {
    read = false
    list = false
  }
}

resource "yandex_ydb_database_serverless" "archives" {
  name      = "${local.name_prefix}db"
  folder_id = var.folder_id

  serverless_database {
    enable_throttling_rcu_limit = true
    throttling_rcu_limit        = 10
    storage_size_limit          = 1
  }
}

output "archive_bucket" {
  description = "Имя закрытого бакета с ZIP-архивами."
  value       = yandex_storage_bucket.archives.bucket
}
