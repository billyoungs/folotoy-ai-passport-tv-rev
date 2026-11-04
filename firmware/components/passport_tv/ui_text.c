#include "ui_text.h"

#include <ui_font_data.h>

#include <string.h>

// Raw surfaces can be byte arrays or DMA buffers. These aliases permit aligned
// stores without assuming that their effective type was uint16_t/uint32_t.
typedef uint16_t ui_halfword_t __attribute__((__may_alias__));
typedef uint32_t ui_word_t __attribute__((__may_alias__));

// The non-ASCII glyphs are one binary blob embedded in the image by the
// component's EMBED_FILES: a glyph count, then that many code points, then an
// advance per glyph, then the glyph bitmaps. It is not a C array because the
// same data written as hex would be about a megabyte of source that every
// rebuild reparses. tools/make_font.py writes it and defines its layout.
// __asm__ rather than asm: the host tests build with -std=c11, where the plain
// spelling is not a keyword and clang rejects it, while the double-underscore
// form is understood by both compilers.
extern const uint8_t ui_font_cjk_blob_start[] __asm__("_binary_ui_font_cjk_bin_start");

// Decode one UTF-8 sequence. Returns the number of bytes consumed and writes the
// code point, or 0 on a malformed or truncated sequence. Only the lengths needed
// for BMP text are accepted; anything else is treated as one replacement cell.
static unsigned utf8_next(const char *text, uint32_t *code)
{
    const unsigned char *p = (const unsigned char *)text;
    if (p[0] < 0x80u) {
        *code = p[0];
        return 1;
    }
    if ((p[0] & 0xE0u) == 0xC0u && (p[1] & 0xC0u) == 0x80u) {
        *code = ((uint32_t)(p[0] & 0x1Fu) << 6) | (uint32_t)(p[1] & 0x3Fu);
        return 2;
    }
    if ((p[0] & 0xF0u) == 0xE0u && (p[1] & 0xC0u) == 0x80u && (p[2] & 0xC0u) == 0x80u) {
        *code = ((uint32_t)(p[0] & 0x0Fu) << 12) | ((uint32_t)(p[1] & 0x3Fu) << 6) |
                (uint32_t)(p[2] & 0x3Fu);
        return 3;
    }
    if ((p[0] & 0xF8u) == 0xF0u && (p[1] & 0xC0u) == 0x80u &&
        (p[2] & 0xC0u) == 0x80u && (p[3] & 0xC0u) == 0x80u) {
        *code = ((uint32_t)(p[0] & 0x07u) << 18) | ((uint32_t)(p[1] & 0x3Fu) << 12) |
                ((uint32_t)(p[2] & 0x3Fu) << 6) | (uint32_t)(p[3] & 0x3Fu);
        return 4;
    }
    *code = 0;
    return 0;
}

#if UI_FONT_CJK_COUNT > 0
typedef struct {
    uint32_t count;
    const uint8_t *codes;    // count * 4 bytes, little-endian, ascending
    const uint8_t *advances; // count bytes
    const uint8_t *glyphs;   // count * UI_GLYPH_BYTES
} cjk_table_t;

static cjk_table_t cjk_table(void)
{
    cjk_table_t table;
    memcpy(&table.count, ui_font_cjk_blob_start, sizeof(table.count));
    table.codes = ui_font_cjk_blob_start + sizeof(table.count);
    table.advances = table.codes + (size_t)table.count * 4u;
    table.glyphs = table.advances + table.count;
    return table;
}

// Binary search, not a scan: the table holds thousands of glyphs, and this runs
// per character of every label on every drawn stripe.
static const uint8_t *cjk_lookup(uint32_t code, int *advance)
{
    cjk_table_t table = cjk_table();
    unsigned low = 0, high = table.count;
    while (low < high) {
        unsigned mid = low + (high - low) / 2u;
        uint32_t value;
        memcpy(&value, table.codes + (size_t)mid * 4u, sizeof(value));
        if (value == code) {
            *advance = table.advances[mid];
            return table.glyphs + (size_t)mid * UI_GLYPH_BYTES;
        }
        if (value < code) {
            low = mid + 1u;
        } else {
            high = mid;
        }
    }
    return NULL;
}
#endif

// Locate a glyph by code point. `*advance` always receives a usable step, so a
// character outside the table advances by a blank cell instead of collapsing
// the line.
static const uint8_t *lookup(uint32_t code, int *advance)
{
    if (code < 0x80u) {
        if (code >= UI_FONT_ASCII_FIRST
                && code < UI_FONT_ASCII_FIRST + UI_FONT_ASCII_COUNT) {
            *advance = ui_font_ascii_adv[code - UI_FONT_ASCII_FIRST];
            return ui_font_ascii[code - UI_FONT_ASCII_FIRST];
        }
        *advance = 8;
        return NULL;
    }
#if UI_FONT_CJK_COUNT > 0
    const uint8_t *glyph = cjk_lookup(code, advance);
    if (glyph) {
        return glyph;
    }
#endif
    // An unlisted character keeps its cell width rather than collapsing the line.
    *advance = UI_GLYPH_W;
    return NULL;
}

// The code point of the next character, with `length` bytes consumed. A malformed
// sequence consumes one byte and keeps that byte as the code point, so the rest
// of the line still lines up.
static unsigned next_code(const char *cursor, uint32_t *code)
{
    unsigned length = utf8_next(cursor, code);
    if (length == 0) {
        *code = (unsigned char)cursor[0];
        length = 1;
    }
    return length;
}

int ui_text_width(const char *text)
{
    if (!text) {
        return 0;
    }
    int width = 0;
    const char *cursor = text;
    while (*cursor) {
        uint32_t code = 0;
        unsigned length = next_code(cursor, &code);
        int advance = 0;
        (void)lookup(code, &advance);
        width += advance;
        cursor += length;
    }
    return width;
}

bool ui_text_truncate(char *out, size_t out_size, const char *text, int limit)
{
    if (!out || out_size == 0) {
        return false;
    }
    out[0] = '\0';
    if (!text) {
        return false;
    }
    size_t used = 0;
    int width = 0;
    const char *cursor = text;
    while (*cursor) {
        uint32_t code = 0;
        unsigned length = next_code(cursor, &code);
        int advance = 0;
        (void)lookup(code, &advance);
        if (width + advance > limit) {
            return false;
        }
        if (used + length >= out_size) {
            return false;
        }
        memcpy(out + used, cursor, length);
        used += length;
        out[used] = '\0';
        width += advance;
        cursor += length;
    }
    return true;
}

void ui_text_fill(ui_surface_t surface, int x, int y, int w, int h, uint16_t color)
{
    if (!surface.pixels || w <= 0 || h <= 0) {
        return;
    }
    int top = y < surface.origin_y ? surface.origin_y : y;
    int bottom = y + h;
    int limit = surface.origin_y + surface.rows;
    if (bottom > limit) {
        bottom = limit;
    }
    int left = x < 0 ? 0 : x;
    int right = x + w;
    if (right > surface.width) {
        right = surface.width;
    }
    if (top >= bottom || left >= right) {
        return;
    }
    uint8_t high = (uint8_t)(color >> 8), low = (uint8_t)(color & 0xFFu);
    for (int row = top; row < bottom; row++) {
        uint8_t *pixel = surface.pixels + ((size_t)(row - surface.origin_y) * surface.width
                                           + (size_t)left) * 2u;
        unsigned bytes=(unsigned)(right-left)*2u;
        if (high==low) {
            memset(pixel,high,bytes);
            continue;
        }
        // Two wire-order pixels per store. Arbitrarily byte-aligned test
        // surfaces retain the portable path; DMA stripes use aligned stores.
        const uint8_t pair[4]={high,low,high,low};
        uint32_t packed;
        memcpy(&packed,pair,sizeof packed);
        if ((uintptr_t)pixel&1u) {
            while (bytes) {*pixel++=high;*pixel++=low;bytes-=2u;}
            continue;
        }
        if (((uintptr_t)pixel&2u) && bytes) {
            *(ui_halfword_t *)pixel=(uint16_t)packed;pixel+=2;bytes-=2;
        }
        while (bytes>=16u) {
            ui_word_t *words=(ui_word_t *)pixel;
            words[0]=packed;words[1]=packed;words[2]=packed;words[3]=packed;
            pixel+=16;bytes-=16;
        }
        while (bytes>=4u) {
            *(ui_word_t *)pixel=packed;pixel+=4;bytes-=4;
        }
        if (bytes) {
            *(ui_halfword_t *)pixel=(uint16_t)packed;
        }
    }
}

int ui_text_draw(ui_surface_t surface, int x, int y, const char *text,
                 uint16_t foreground, uint16_t background)
{
    if (!text) {
        return x;
    }
    // The background box is painted first so the glyphs sit on a known colour,
    // which is what keeps them readable when the video behind them is bright.
    int width = ui_text_width(text);
    ui_text_fill(surface, x - UI_TEXT_PAD_X, y - UI_TEXT_PAD_Y,
                 width + 2 * UI_TEXT_PAD_X, (int)UI_TEXT_LINE_H + 2 * UI_TEXT_PAD_Y,
                 background);

    int pen = x;
    const char *cursor = text;
    while (*cursor) {
        uint32_t code = 0;
        unsigned length = next_code(cursor, &code);
        int advance = 0;
        const uint8_t *glyph = lookup(code, &advance);
        if (glyph) {
            for (unsigned gy = 0; gy < UI_GLYPH_H; gy++) {
                uint16_t bits = (uint16_t)((glyph[gy * 2u] << 8) | glyph[gy * 2u + 1u]);
                if (!bits) {
                    continue;  // blank row: the box already painted it
                }
                // Fill the runs of set bits in one call each. Drawing pixel by
                // pixel cost a function call and a bounds check per dot, which
                // made the overlay expensive enough that the idle task stopped
                // being scheduled and the watchdog fired.
                unsigned run_start = 0;
                while (run_start < UI_GLYPH_W) {
                    if (!(bits & (uint16_t)(1u << (UI_GLYPH_W - 1u - run_start)))) {
                        run_start++;
                        continue;
                    }
                    unsigned run_end = run_start;
                    while (run_end < UI_GLYPH_W &&
                           (bits & (uint16_t)(1u << (UI_GLYPH_W - 1u - run_end)))) {
                        run_end++;
                    }
                    ui_text_fill(surface, pen + (int)run_start, y + (int)gy,
                                 (int)(run_end - run_start), 1, foreground);
                    run_start = run_end;
                }
            }
        }
        pen += advance;
        cursor += length;
    }
    return pen;
}

void ui_text_prepare(ui_text_run_t *run, const char *text, unsigned max_width)
{
    memset(run,0,sizeof(*run));
    if (!text) return;
    while (*text && run->count<UI_TEXT_RUN_MAX) {
        uint32_t code;
        unsigned bytes=next_code(text,&code);
        int advance;
        const uint8_t *glyph=lookup(code,&advance);
        if ((unsigned)run->width+(unsigned)advance>max_width) break;
        uint16_t id=advance==8?0xffffu:0xfffeu;
        if (glyph) {
            if (code<0x80u) id=0x8000u|(uint16_t)(code-UI_FONT_ASCII_FIRST);
#if UI_FONT_CJK_COUNT > 0
            else id=(uint16_t)((glyph-cjk_table().glyphs)/UI_GLYPH_BYTES);
#endif
        }
        run->glyphs[run->count++]=id;
        run->width+=(uint16_t)advance;
        text+=bytes;
    }
}

void ui_text_paint(ui_surface_t surface, int x, int y, const ui_text_run_t *run,
                   uint16_t color)
{
    if (!surface.pixels || !run || surface.rows<=0 || surface.width<=0) return;
    int top=surface.origin_y>y?surface.origin_y:y;
    int bottom=surface.origin_y+surface.rows;
    if (bottom>y+(int)UI_GLYPH_H) bottom=y+(int)UI_GLYPH_H;
    if (top>=bottom || x>=surface.width || x+(int)run->width<=0) return;
#if UI_FONT_CJK_COUNT > 0
    cjk_table_t table=cjk_table();
#endif
    for (unsigned i=0;i<run->count;i++) {
        unsigned id=run->glyphs[i];
        const uint8_t *glyph=NULL;
        int advance;
        if (id==0xffffu || id==0xfffeu) advance=id==0xffffu?8:16;
        else if (id&0x8000u) {
            unsigned at=id&0x7fffu;
            if (at>=UI_FONT_ASCII_COUNT) return;
            glyph=ui_font_ascii[at];advance=ui_font_ascii_adv[at];
        } else {
#if UI_FONT_CJK_COUNT > 0
            if (id>=table.count) return;
            glyph=table.glyphs+id*UI_GLYPH_BYTES;advance=table.advances[id];
#else
            advance=16;
#endif
        }
        if (glyph && x+(int)UI_GLYPH_W>0 && x<surface.width) {
            int left=x<0?-x:0;
            int right=surface.width-x;
            if (right>(int)UI_GLYPH_W) right=(int)UI_GLYPH_W;
            for (int row=top;row<bottom;row++) {
                unsigned gy=(unsigned)(row-y);
                uint16_t bits=(uint16_t)((glyph[gy*2u]<<8)|glyph[gy*2u+1u]);
                if (!bits) continue;
                uint8_t *dst=surface.pixels+((size_t)(row-surface.origin_y)*surface.width+(x+left))*2u;
                for (int col=left;col<right;col++,dst+=2) {
                    if (bits&(uint16_t)(0x8000u>>col)) {
                        dst[0]=(uint8_t)(color>>8);dst[1]=(uint8_t)color;
                    }
                }
            }
        }
        x+=advance;
        if (x>=surface.width) break;
    }
}
