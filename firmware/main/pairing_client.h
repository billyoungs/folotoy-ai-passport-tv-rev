#pragma once
#include "tv_config.h"
#include "esp_err.h"

// Build policy only; public builds require the user to enter an origin.
const char *pairing_client_default_origin(void);
// Only call for the old managed private configuration, before using credentials.
esp_err_t pairing_client_migrate(void);
// Recover a saved pending/active binding before redeeming a supplied code.
// Empty code may only refresh an existing binding at this exact origin.
esp_err_t pairing_client_pair(const char *origin,const char *code,tv_connection_t *out,bool *pending_used);
esp_err_t pairing_client_refresh(const char *origin,tv_connection_t *out,uint32_t timeout_ms);
// Call only after durable state save, and only when pair reports pending_used.
esp_err_t pairing_client_committed(const char *origin);
