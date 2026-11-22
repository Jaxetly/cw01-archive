resource "yandex_iam_service_account" "queue_manager" {
  folder_id   = var.folder_id
  name        = "${local.name_prefix}ymq-admin"
  description = "Manages the archive queues during Terraform runs"
}

resource "yandex_resourcemanager_folder_iam_member" "queue_manager" {
  folder_id = var.folder_id
  role      = "ymq.admin"
  member    = "serviceAccount:${yandex_iam_service_account.queue_manager.id}"
}

# The Message Queue Terraform resource uses the SQS API and signs requests.
resource "yandex_iam_service_account_static_access_key" "queue_manager" {
  service_account_id = yandex_iam_service_account.queue_manager.id
  description        = "Terraform access to the archive queues"

  depends_on = [yandex_resourcemanager_folder_iam_member.queue_manager]
}

resource "yandex_message_queue" "dead_letter" {
  name                      = "${local.name_prefix}dlq"
  message_retention_seconds = 1209600
  access_key                = yandex_iam_service_account_static_access_key.queue_manager.access_key
  secret_key                = yandex_iam_service_account_static_access_key.queue_manager.secret_key
}

resource "yandex_message_queue" "tasks" {
  name                       = "${local.name_prefix}tasks"
  visibility_timeout_seconds = 900
  receive_wait_time_seconds  = 20
  message_retention_seconds  = 86400
  access_key                 = yandex_iam_service_account_static_access_key.queue_manager.access_key
  secret_key                 = yandex_iam_service_account_static_access_key.queue_manager.secret_key

  redrive_policy = jsonencode({
    deadLetterTargetArn = yandex_message_queue.dead_letter.arn
    maxReceiveCount     = 1
  })
}
