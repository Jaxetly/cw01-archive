variable "cloud_id" {
  type        = string
  nullable    = false
  description = "Идентификатор облака Yandex Cloud."
}

variable "folder_id" {
  type        = string
  nullable    = false
  description = "Идентификатор каталога Yandex Cloud в указанном облаке."
}

variable "bucket_name" {
  type        = string
  nullable    = false
  description = "Глобально уникальное имя нового бакета, начинающееся с vvotNN-."

  validation {
    condition = (
      can(regex("^vvot[0-9]{2}-[a-z0-9][a-z0-9.-]*$", var.bucket_name)) &&
      can(regex("[a-z0-9]$", var.bucket_name)) &&
      length(var.bucket_name) <= 63 &&
      !strcontains(var.bucket_name, "..")
    )
    error_message = "Укажите имя нового бакета до 63 символов с префиксом vvotNN-, например vvot00-archive-example."
  }
}
