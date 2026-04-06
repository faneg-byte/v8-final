variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-east4"
}

variable "billing_account" {
  type = string
}

variable "db_user" {
  type    = string
  default = "v8operator"
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "telegram_bot_token" {
  type      = string
  sensitive = true
  default   = ""
}

variable "telegram_chat_id" {
  type    = string
  default = ""
}
