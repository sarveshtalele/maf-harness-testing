variable "subscription_id" { type = string }
variable "tenant_id" { type = string }
variable "entra_client_id" { type = string }
variable "entra_secret_uri" {
  type        = string
  description = "Existing Key Vault secret URI, never the secret value. Grant web app identity access separately."
  validation {
    condition     = can(regex("^https://[a-zA-Z0-9-]+\\.vault\\.azure\\.net/secrets/[^/]+(/[^/]+)?/?$", var.entra_secret_uri))
    error_message = "Supply an Azure Key Vault secret URI."
  }
}
variable "allowed_user_object_ids" {
  type = list(string)
  validation {
    condition     = length(var.allowed_user_object_ids) > 0
    error_message = "Explicitly list approved Entra user object IDs."
  }
}
variable "name" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,49}$", var.name))
    error_message = "Use a globally unique lowercase app name, 3–50 characters."
  }
}
variable "location" { type = string }
variable "sku" {
  type    = string
  default = "B1"
  validation {
    condition     = contains(["B1", "B2", "B3"], var.sku)
    error_message = "Choose a Linux Basic SKU: B1, B2 or B3."
  }
}
variable "image_name" {
  type        = string
  description = "Public GHCR image path pinned by sha256 digest, without registry prefix. Must implement hosted-mode gates."
  validation {
    condition     = can(regex("^[a-z0-9/_-]+@sha256:[a-f0-9]{64}$", var.image_name))
    error_message = "Pin a public image by its immutable sha256 digest."
  }
}
variable "enable_public_access" {
  type    = bool
  default = false
}
variable "allowed_cidrs" {
  type    = list(string)
  default = []
  validation {
    condition     = alltrue([for cidr in var.allowed_cidrs : can(cidrhost(cidr, 0)) && cidr != "0.0.0.0/0" && cidr != "::/0"])
    error_message = "Use valid team CIDRs; unrestricted networks are not accepted."
  }
}
