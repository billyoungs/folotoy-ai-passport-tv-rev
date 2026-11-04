#include "passport_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_wifi_default.h"
#include "nvs_flash.h"
#include "esp_log.h"
#include <string.h>
#include <stdio.h>
#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_DISCONNECTED_BIT BIT1
static bool s_netif_ready, s_loop_ready, s_wifi_initialized, s_wifi_started;
static esp_netif_t *s_sta, *s_ap;
static EventGroupHandle_t s_wifi_events;
static esp_event_handler_instance_t s_wifi_handler, s_ip_handler;
static atomic_int s_reason;
static atomic_bool s_auto_reconnect;
void passport_wifi_auto_reconnect(bool enabled){atomic_store(&s_auto_reconnect,enabled);}

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *ev = data;
        atomic_store(&s_reason, ev ? ev->reason : 0);
        xEventGroupClearBits(s_wifi_events, WIFI_CONNECTED_BIT);
        xEventGroupSetBits(s_wifi_events, WIFI_DISCONNECTED_BIT);
        if(atomic_load(&s_auto_reconnect))(void)esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(s_wifi_events, WIFI_CONNECTED_BIT);
    }
}

// Checked setup using the board reference's lifecycle; event storage predates callbacks.
static esp_err_t wifi_prepare(void) {
    if (s_wifi_initialized) return ESP_OK;
    esp_err_t e = nvs_flash_init();
    if (e != ESP_OK) return e;
    if (!s_netif_ready) {
        e = esp_netif_init(); if (e != ESP_OK) return e; s_netif_ready = true;
    }
    if (!s_loop_ready) {
        e = esp_event_loop_create_default(); if (e != ESP_OK) return e; s_loop_ready = true;
    }
    if (!s_wifi_events) s_wifi_events = xEventGroupCreate();
    if (!s_wifi_events) return ESP_ERR_NO_MEM;
    esp_netif_config_t nc = ESP_NETIF_DEFAULT_WIFI_STA();
    s_sta = esp_netif_new(&nc);
    if (!s_sta) return ESP_ERR_NO_MEM;
    e = esp_netif_attach_wifi_station(s_sta);
    if (e == ESP_OK) e = esp_wifi_set_default_wifi_sta_handlers();
    bool initialized = false;
    if (e == ESP_OK) {
        wifi_init_config_t ic = WIFI_INIT_CONFIG_DEFAULT();
        e = esp_wifi_init(&ic); initialized = e == ESP_OK;
    }
    if (e == ESP_OK) e = esp_wifi_set_storage(WIFI_STORAGE_RAM);
    // Modem sleep batches downlink PCM behind beacon wakeups.
    if (e == ESP_OK) e = esp_wifi_set_ps(WIFI_PS_NONE);
    if (e == ESP_OK) e = esp_event_handler_instance_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, wifi_event, NULL, &s_wifi_handler);
    if (e == ESP_OK) e = esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL, &s_ip_handler);
    if (e == ESP_OK) { s_wifi_initialized = true; return ESP_OK; }
    if (s_ip_handler) { esp_event_handler_instance_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP, s_ip_handler); s_ip_handler = NULL; }
    if (s_wifi_handler) { esp_event_handler_instance_unregister(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, s_wifi_handler); s_wifi_handler = NULL; }
    if (initialized) esp_wifi_deinit();
    esp_netif_destroy_default_wifi(s_sta); s_sta = NULL;
    return e;
}

bool passport_wifi_is_online(void) {
    return s_wifi_events && (xEventGroupGetBits(s_wifi_events) & WIFI_CONNECTED_BIT);
}
int passport_wifi_rssi(void) {
    wifi_ap_record_t ap;
    return esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
}
void passport_wifi_status(passport_wifi_status_t *status) {
    memset(status,0,sizeof *status);
    wifi_ap_record_t ap;
    if(esp_wifi_sta_get_ap_info(&ap)!=ESP_OK)return;
    status->connected=true;status->rssi=ap.rssi;
    memcpy(status->ssid,ap.ssid,32);
    esp_netif_ip_info_t ip={0};
    if(s_sta && esp_netif_get_ip_info(s_sta,&ip)==ESP_OK && ip.ip.addr)
        snprintf(status->ipv4,sizeof status->ipv4,IPSTR,IP2STR(&ip.ip));
}
int passport_wifi_disconnect_reason(void) { return atomic_load(&s_reason); }
static void wifi_disconnect(void) {
    // The worker serializes connection attempts. Callbacks only report status.
    xEventGroupClearBits(s_wifi_events, WIFI_DISCONNECTED_BIT);
    if (esp_wifi_disconnect() == ESP_OK)
        xEventGroupWaitBits(s_wifi_events, WIFI_DISCONNECTED_BIT, pdTRUE, pdFALSE, pdMS_TO_TICKS(300));
    xEventGroupClearBits(s_wifi_events, WIFI_CONNECTED_BIT | WIFI_DISCONNECTED_BIT);
}

static esp_err_t wifi_connect_one(const passport_wifi_profile_t *profile, int timeout_ms) {
    atomic_store(&s_reason, 0);
    wifi_config_t wc = {0};
    memcpy(wc.sta.ssid, profile->ssid, strlen(profile->ssid));
    memcpy(wc.sta.password, profile->password, strlen(profile->password));
    wc.sta.threshold.authmode = profile->password[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    esp_err_t e = esp_wifi_set_config(WIFI_IF_STA, &wc);
    if (e == ESP_OK) e = esp_wifi_connect();
    if (e != ESP_OK) return e;
    EventBits_t bits = xEventGroupWaitBits(s_wifi_events, WIFI_CONNECTED_BIT | WIFI_DISCONNECTED_BIT,
                                          pdFALSE, pdFALSE, pdMS_TO_TICKS(timeout_ms));
    return (bits & WIFI_CONNECTED_BIT) ? ESP_OK : (bits & WIFI_DISCONNECTED_BIT) ? ESP_FAIL : ESP_ERR_TIMEOUT;
}

esp_err_t passport_wifi_connect(const passport_wifi_profile_t *profiles,unsigned count,int timeout_ms,unsigned *selected) {
    if(!profiles || !count || count>PASSPORT_WIFI_MAX_NETWORKS || timeout_ms<=0)return ESP_ERR_INVALID_ARG;
    esp_err_t e=wifi_prepare();if(e!=ESP_OK)return e;
    if(!s_wifi_started){
        e=esp_wifi_set_mode(WIFI_MODE_STA);
        if(e==ESP_OK)e=esp_wifi_start();
        if(e!=ESP_OK)return e;
        s_wifi_started=true;
    }
    wifi_disconnect();
    int rssi[PASSPORT_WIFI_MAX_NETWORKS];
    for (unsigned i = 0; i < count; i++) rssi[i] = -128;
    wifi_scan_config_t scan = {.show_hidden = true};
    if (esp_wifi_scan_start(&scan, true) == ESP_OK) {
        wifi_ap_record_t ap;
        while (esp_wifi_scan_get_ap_record(&ap) == ESP_OK) {
            for (unsigned i = 0; i < count; i++)
                if (!strcmp(profiles[i].ssid, (const char *)ap.ssid) && ap.rssi > rssi[i]) rssi[i] = ap.rssi;
        }
    }
    // Release every scan record, including on a partial read or scan failure.
    esp_wifi_clear_ap_list();
    for (unsigned attempt = 0; attempt < count; attempt++) {
        unsigned best = 0;
        for (unsigned i = 1; i < count; i++) if (rssi[i] > rssi[best]) best = i;
        e = wifi_connect_one(&profiles[best], timeout_ms);
        if (e == ESP_OK) {
            if(selected)*selected=best;
            return ESP_OK;
        }
        wifi_disconnect();
        rssi[best] = -129;
    }
    return e;
}

esp_err_t passport_wifi_provision_prepare(void) {
    esp_err_t e = wifi_prepare();
    if (e != ESP_OK) return e;
    if (!s_ap) {
        esp_netif_config_t nc = ESP_NETIF_DEFAULT_WIFI_AP();
        s_ap = esp_netif_new(&nc);
        if (!s_ap) return ESP_ERR_NO_MEM;
        e = esp_netif_attach_wifi_ap(s_ap);
        if (e == ESP_OK) e = esp_wifi_set_default_wifi_ap_handlers();
        // Advertise only the setup interface as DNS; leave the station resolver unchanged.
        esp_netif_ip_info_t info;
        if (e == ESP_OK) e = esp_netif_get_ip_info(s_ap, &info);
        esp_netif_dns_info_t dns = {.ip.type=ESP_IPADDR_TYPE_V4};
        if (e == ESP_OK) { dns.ip.u_addr.ip4 = info.ip; e = esp_netif_set_dns_info(s_ap, ESP_NETIF_DNS_MAIN, &dns); }
        uint8_t offer_dns = 1;
        if (e == ESP_OK) e = esp_netif_dhcps_option(s_ap, ESP_NETIF_OP_SET, ESP_NETIF_DOMAIN_NAME_SERVER, &offer_dns, sizeof offer_dns);
        if (e != ESP_OK) { esp_netif_destroy_default_wifi(s_ap); s_ap = NULL; return e; }
    }
    // The official provisioning manager owns Wi-Fi start/mode/connect from here.
    return ESP_OK;
}

esp_err_t passport_wifi_provision_start_ap(const char *name) {
    wifi_config_t config = {.ap = {.max_connection = 3, .authmode = WIFI_AUTH_OPEN}};
    size_t size = strlen(name);
    if (!size || size > sizeof config.ap.ssid) return ESP_ERR_INVALID_ARG;
    memcpy(config.ap.ssid, name, size);
    config.ap.ssid_len = size;
    esp_err_t e = esp_wifi_set_mode(WIFI_MODE_APSTA);
    if (e == ESP_OK) e = esp_wifi_set_config(WIFI_IF_AP, &config);
    return e;
}

void passport_wifi_provision_cleanup(void) {
    esp_wifi_set_mode(WIFI_MODE_STA);
    if (s_ap) { esp_netif_destroy_default_wifi(s_ap); s_ap = NULL; }
    s_wifi_started = true;
    esp_wifi_set_storage(WIFI_STORAGE_RAM);
}

esp_err_t passport_wifi_setup_ap(const char *name){
    esp_err_t e=passport_wifi_provision_prepare();if(e!=ESP_OK)return e;
    e=passport_wifi_provision_start_ap(name); // Same WIFI_AUTH_OPEN policy as FoloTalk.
    if(e==ESP_OK && !s_wifi_started){e=esp_wifi_start();if(e==ESP_OK)s_wifi_started=true;}
    return e;
}
