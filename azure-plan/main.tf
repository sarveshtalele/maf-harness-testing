terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}
provider "azurerm" {
  subscription_id = var.subscription_id
  features {}
}
resource "azurerm_resource_group" "harness" {
  name     = "${var.name}-rg"
  location = var.location
  tags     = { project = "maf-harness", branch = "new-ui" }
}
resource "azurerm_service_plan" "harness" {
  name                = "${var.name}-plan"
  resource_group_name = azurerm_resource_group.harness.name
  location            = azurerm_resource_group.harness.location
  os_type             = "Linux"
  sku_name            = var.sku
  worker_count        = 1
}
resource "azurerm_linux_web_app" "harness" {
  name                                           = var.name
  resource_group_name                            = azurerm_resource_group.harness.name
  location                                       = azurerm_resource_group.harness.location
  service_plan_id                                = azurerm_service_plan.harness.id
  https_only                                     = true
  public_network_access_enabled                  = var.enable_public_access
  ftp_publish_basic_authentication_enabled       = false
  webdeploy_publish_basic_authentication_enabled = false
  identity { type = "SystemAssigned" }
  site_config {
    always_on                         = true
    minimum_tls_version               = "1.2"
    scm_minimum_tls_version           = "1.2"
    ftps_state                        = "Disabled"
    http2_enabled                     = true
    ip_restriction_default_action     = "Deny"
    scm_use_main_ip_restriction       = true
    scm_ip_restriction_default_action = "Deny"
    application_stack {
      docker_image_name   = var.image_name
      docker_registry_url = "https://ghcr.io"
    }
    dynamic "ip_restriction" {
      for_each = { for index, cidr in var.allowed_cidrs : index => cidr }
      content {
        name       = "team-${ip_restriction.key}"
        ip_address = ip_restriction.value
        priority   = 100 + tonumber(ip_restriction.key)
        action     = "Allow"
      }
    }
  }
  auth_settings_v2 {
    auth_enabled           = true
    require_authentication = true
    require_https          = true
    default_provider       = "azureactivedirectory"
    unauthenticated_action = "RedirectToLoginPage"
    active_directory_v2 {
      client_id                  = var.entra_client_id
      tenant_auth_endpoint       = "https://login.microsoftonline.com/${var.tenant_id}/v2.0/"
      client_secret_setting_name = "ENTRA_CLIENT_SECRET"
      allowed_identities         = var.allowed_user_object_ids
    }
    login { token_store_enabled = true }
  }
  app_settings = {
    WEBSITES_PORT                       = "8080"
    WEBSITES_ENABLE_APP_SERVICE_STORAGE = "true"
    HARNESS_RUNS_DIR                    = "/home/maf-harness/runs"
    HARNESS_EXECUTION_MODE              = "disabled"
    HARNESS_DEPLOYMENT_MODE             = "azure"
    HARNESS_PUBLIC_ORIGIN               = "https://${var.name}.azurewebsites.net"
    NEXT_TELEMETRY_DISABLED             = "1"
    ENTRA_CLIENT_SECRET                 = "@Microsoft.KeyVault(SecretUri=${var.entra_secret_uri})"
  }
  lifecycle {
    precondition {
      condition     = !var.enable_public_access || length(var.allowed_cidrs) > 0
      error_message = "Public access requires explicit team CIDRs."
    }
  }
}
output "application_url" { value = "https://${azurerm_linux_web_app.harness.default_hostname}" }
output "managed_identity_object_id" { value = azurerm_linux_web_app.harness.identity[0].principal_id }
