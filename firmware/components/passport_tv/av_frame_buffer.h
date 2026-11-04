#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>
#include "av_protocol.h"

#define AV_FRAME_REGIONS 4
#define AV_FRAME_RING_SPAN 65536u
typedef struct { uint8_t *data; uint32_t size; } av_frame_region_t;
typedef struct {
    av_frame_region_t regions[AV_FRAME_REGIONS];
    uint32_t capacity, head;
    atomic_uint_least32_t tail;
} av_frame_ring_t;
typedef struct {
    uint8_t *data;
    uint32_t data_start, release_end;
    uint16_t length;
    uint8_t stripe;
    uint32_t pts;
    bool frame_start, packet_start, packet_end, dropped;
} av_frame_stripe_t;
typedef struct {
    av_frame_stripe_t stripes[AV_STRIPES];
    uint32_t pts, mark, bytes;
    uint8_t count;
} av_complete_frame_t;

// One receiver owns head; the display releases committed stripes in order.
// Regions occupy a power-of-two virtual ring; unmapped bytes are padding.
// Unsigned cursor wrap therefore preserves the physical mapping indefinitely.
bool av_frame_ring_init(av_frame_ring_t *r, const av_frame_region_t regions[AV_FRAME_REGIONS]);
bool av_frame_ring_alloc(av_frame_ring_t *r, uint32_t bytes, av_frame_stripe_t *stripe);
// Advance through physical regions without requiring a contiguous stripe.
uint32_t av_frame_ring_span(const av_frame_ring_t *r,uint32_t *cursor,
                           uint32_t remaining,uint8_t **data);
void av_frame_ring_release(av_frame_ring_t *r, uint32_t end);
// Rewind only the receiver's current frame, which has never been published.
void av_frame_ring_abort(av_frame_ring_t *r, uint32_t mark);
bool av_frame_packet_matches(const av_complete_frame_t *frame, uint32_t pts,
                             unsigned first, unsigned count, bool start);
bool av_frame_complete(const av_complete_frame_t *frame);
