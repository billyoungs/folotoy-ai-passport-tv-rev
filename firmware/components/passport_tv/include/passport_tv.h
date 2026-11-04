#pragma once
// Passport TV: the shared FAV1 live-TV player for standalone and embedded hosts.
//
// The host prepares exclusive display, codec and workspace ownership.
// FoloTalk closes its social link and pauses LVGL; standalone hosts need neither.
// TV then owns the panel
// (raw landscape mode), the codec and the keys until it finishes and calls the
// done callback from its own task.
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define FT_TV_HOST_MAX 64
#define FT_TV_TICKET_MAX 128
#define FT_TV_CONNECTION_TIMEOUT_MS 25000u
#define FT_TV_SESSION_STACK_DEFAULT 4096u
#define FT_TV_SESSION_STACK_MAX 16384u
#define FT_TV_STRIPE_BYTES (320u * 12u * 2u * 2u)  // two landscape stripes
#define FT_TV_CONTROL_BYTES 4096u                  // CONFIG JSON (40 channels)
#define FT_TV_WORKSPACE_BYTES 10240u               // CONFIG, then compressed video ring

typedef void (*ft_tv_done_cb_t)(void);
typedef void (*ft_tv_settings_cb_t)(uint8_t volume, uint8_t brightness);

// Capacities include the terminating NUL. Host is an IPv4 literal or DNS name
// (ASCII letters/digits/hyphens, nonempty labels); port must be nonzero.
typedef struct {
    char host[FT_TV_HOST_MAX];
    uint16_t port;
    char ticket[FT_TV_TICKET_MAX]; // 1..127 printable ASCII bytes, no spaces
} ft_tv_connection_t;

// Called synchronously on the TV owner task once before each session, including
// reconnects/channel/quality changes, after the previous media workers drain.
// Fill the zeroed, temporary out and return ESP_OK; failure never uses the old
// credentials. Do not retain out. Release HTTPS resources before returning and
// bound ALL work (DNS/time sync/TLS/retries included) by timeout_ms, not each I/O.
// The core cannot preempt this call: queued exit keys are handled on return, at
// most FT_TV_CONNECTION_TIMEOUT_MS later for a conforming provider. ft_tv_stop()
// may also be called concurrently; stopped/late results are discarded.
// Poll ft_tv_connection_cancelled() between bounded I/O/waits (prefer <=100 ms)
// to service keys/display during authentication, and abort/clean up when true.
typedef esp_err_t (*ft_tv_connection_cb_t)(void *context, ft_tv_connection_t *out,
                                         uint32_t timeout_ms);

typedef struct {
    const char *host;          // TV service host
    uint16_t port;
    const char *ticket;        // authentication payload: signed ticket or standalone pairing token
    uint8_t *stripes;          // >= FT_TV_STRIPE_BYTES, DMA-capable (LVGL draw buffer)
    size_t stripes_bytes;
    uint8_t *control;          // >= FT_TV_WORKSPACE_BYTES, borrowed until done()
    size_t control_bytes;
    uint8_t *frame_extra;      // borrowed LVGL allocator block; no system heap growth
    size_t frame_extra_bytes;
    uint8_t volume, brightness;
    ft_tv_done_cb_t done;      // called from the TV task after everything is released
    ft_tv_settings_cb_t settings;
    bool (*prepare)(void);     // optional caller-workspace handoff before media allocation
    void (*allocation_failed)(size_t bytes, uint32_t caps, const char *function); // optional host OOM recorder
    // Optional refresh replaces host/port/ticket; those fields may be NULL/0
    // when set. NULL preserves the legacy static credentials flow.
    ft_tv_connection_cb_t connection_provider;
    void *connection_provider_ctx; // borrowed until done(); callback/context copied at start
    // 0 selects 4096; otherwise 4096..16384 bytes, multiple of 4. HTTPS hosts
    // should budget their callback's stack here (e.g. 10240), not a media stack.
    uint32_t session_task_stack_bytes;
} ft_tv_config_t;

// Key events go here while TV runs (bsp_btn_t, bsp_btn_ev_t as ints).
void ft_tv_key(int key, int event);
// Queue a metadata-only menu snapshot for local USB acceptance tooling.
void ft_tv_inspect(void);
esp_err_t ft_tv_start(const ft_tv_config_t *config);
// Ask TV to leave. Returns at once; done() follows when the session is drained.
void ft_tv_stop(void);
bool ft_tv_running(void);
// Only call synchronously from connection_provider on the TV task. Services
// queued keys and the waiting screen; true means discard this session attempt.
bool ft_tv_connection_cancelled(void);

// Product-neutral names; legacy ft_tv_* entry points remain source-compatible.
typedef ft_tv_config_t passport_tv_config_t;
typedef ft_tv_connection_t passport_tv_connection_t;
typedef ft_tv_connection_cb_t passport_tv_connection_cb_t;
#define PASSPORT_TV_HOST_MAX FT_TV_HOST_MAX
#define PASSPORT_TV_AUTH_MAX FT_TV_TICKET_MAX
#define PASSPORT_TV_CONNECTION_TIMEOUT_MS FT_TV_CONNECTION_TIMEOUT_MS
#define PASSPORT_TV_SESSION_STACK_DEFAULT FT_TV_SESSION_STACK_DEFAULT
#define PASSPORT_TV_SESSION_STACK_MAX FT_TV_SESSION_STACK_MAX
#define PASSPORT_TV_STRIPE_BYTES FT_TV_STRIPE_BYTES
#define PASSPORT_TV_WORKSPACE_BYTES FT_TV_WORKSPACE_BYTES
static inline esp_err_t passport_tv_start(const passport_tv_config_t *c) { return ft_tv_start(c); }
static inline void passport_tv_stop(void) { ft_tv_stop(); }
static inline bool passport_tv_running(void) { return ft_tv_running(); }
static inline bool passport_tv_connection_cancelled(void) { return ft_tv_connection_cancelled(); }
static inline void passport_tv_key(int key, int event) { ft_tv_key(key,event); }
