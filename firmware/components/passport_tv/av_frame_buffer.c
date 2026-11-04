#include "av_frame_buffer.h"
#include <string.h>

bool av_frame_ring_init(av_frame_ring_t *r, const av_frame_region_t regions[AV_FRAME_REGIONS]) {
    memset(r,0,sizeof(*r));
    atomic_init(&r->tail,0);
    for(unsigned i=0;i<AV_FRAME_REGIONS;i++) {
        if(!regions[i].size) continue;
        if(!regions[i].data || !regions[i].size ||
           ((uintptr_t)regions[i].data&3u) || (regions[i].size&3u)) return false;
        if(regions[i].size>AV_FRAME_RING_SPAN-r->capacity) return false;
        r->regions[i]=regions[i]; r->capacity+=regions[i].size;
    }
    return true;
}

uint32_t av_frame_ring_span(const av_frame_ring_t *r,uint32_t *cursor,
                           uint32_t remaining,uint8_t **data) {
    if(!remaining || !r->capacity)return 0;
    uint32_t pos=*cursor&(AV_FRAME_RING_SPAN-1u),base=0;
    if(pos>=r->capacity) { *cursor+=AV_FRAME_RING_SPAN-pos;pos=0; }
    for(unsigned i=0;i<AV_FRAME_REGIONS;i++) {
        uint32_t end=base+r->regions[i].size;
        if(pos<end) {
            uint32_t n=end-pos;if(n>remaining)n=remaining;
            *data=r->regions[i].data+(pos-base);*cursor+=n;return n;
        }
        base=end;
    }
    return 0;
}
bool av_frame_ring_alloc(av_frame_ring_t *r,uint32_t bytes,av_frame_stripe_t *stripe) {
    if(!bytes || bytes>4096u || bytes>r->capacity) return false;
    uint32_t next=r->head, left=(bytes+3u)&~3u, start=0;
    uint8_t *first=NULL;
    while(left) {
        uint8_t *part=NULL;
        uint32_t n=av_frame_ring_span(r,&next,left,&part);
        if(!n)return false;
        if(!first) { first=part;start=next-n; }
        left-=n;
    }
    if((uint32_t)(next-atomic_load_explicit(&r->tail,memory_order_acquire))>
       AV_FRAME_RING_SPAN)return false;
    stripe->data=first;stripe->data_start=start;stripe->release_end=next;
    r->head=next;return true;
}
void av_frame_ring_release(av_frame_ring_t *r,uint32_t end) {
    atomic_store_explicit(&r->tail,end,memory_order_release);
}
void av_frame_ring_abort(av_frame_ring_t *r,uint32_t mark) { r->head=mark; }

bool av_frame_packet_matches(const av_complete_frame_t *f,uint32_t pts,
                             unsigned first,unsigned count,bool start) {
    return count && first<AV_STRIPES && count<=AV_STRIPES-first &&
        first==f->count && (start ? first==0 : first!=0 && pts==f->pts);
}
bool av_frame_complete(const av_complete_frame_t *f) {
    return f->count==AV_STRIPES;
}
