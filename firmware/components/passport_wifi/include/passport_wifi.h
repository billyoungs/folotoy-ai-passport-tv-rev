#pragma once
#include "esp_err.h"
#include <stdbool.h>
#include <stdint.h>
#define PASSPORT_WIFI_MAX_NETWORKS 5
// Same bounded remembered-network shape as FoloTalk; credentials are host-owned.
typedef struct {char ssid[33],password[65];} passport_wifi_profile_t;
typedef struct {bool connected;char ssid[33];int rssi;char ipv4[16];} passport_wifi_status_t;
esp_err_t passport_wifi_connect(const passport_wifi_profile_t *profiles,unsigned count,int timeout_ms,unsigned *selected);
bool passport_wifi_is_online(void);
int passport_wifi_rssi(void);
int passport_wifi_disconnect_reason(void);
void passport_wifi_status(passport_wifi_status_t *out);
void passport_wifi_auto_reconnect(bool enabled);
// FoloTalk provisioning manager retains ownership of Wi-Fi start/stop.
esp_err_t passport_wifi_provision_prepare(void);
esp_err_t passport_wifi_provision_start_ap(const char *name);
void passport_wifi_provision_cleanup(void);
// Standalone setup keeps STA up and adds the same open AP; no account operations.
esp_err_t passport_wifi_setup_ap(const char *name);
