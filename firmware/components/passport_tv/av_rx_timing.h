#pragma once
#include <stdint.h>
#include <stdatomic.h>

enum { RX_SOCKET_SELECT, RX_SOCKET_CALL, RX_AUDIO_QUEUE, RX_FRAME_RING,
       RX_FRAME_QUEUE, RX_PHASES };
typedef struct {
    atomic_uint_least32_t total_us, max_us;
} av_rx_timing_t;

static inline void av_rx_timing_add(av_rx_timing_t *v,uint32_t us) {
    atomic_fetch_add_explicit(&v->total_us,us,memory_order_relaxed);
    uint_least32_t old=atomic_load_explicit(&v->max_us,memory_order_relaxed);
    while(old<us && !atomic_compare_exchange_weak_explicit(&v->max_us,&old,us,
          memory_order_relaxed,memory_order_relaxed)) {}
}
