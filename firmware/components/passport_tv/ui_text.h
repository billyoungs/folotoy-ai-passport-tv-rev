// Bitmap text and rectangle drawing over a stripe of the panel. No ESP-IDF
// dependencies, so the layout maths is host-testable.
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// A band of the panel the caller owns and is about to push. Pixels are RGB565
// with the high byte first, `width` pixels per row, and the band covers
// destination rows [origin_y, origin_y + rows). Anything drawn outside is
// clipped, which is what makes partial-height stripes safe to draw into.
typedef struct {
    uint8_t *pixels;
    int width;
    int origin_y;
    int rows;
} ui_surface_t;

// Glyph cell height. A line of text occupies exactly this many rows.
#define UI_TEXT_LINE_H 16u
// Horizontal gap between the text and the edge of its background box.
#define UI_TEXT_PAD_X 6
#define UI_TEXT_PAD_Y 4

// Opaque RGB565 colours. Names describe the role, not the shade: the palette is
// deliberately small so overlays stay legible over arbitrary video.
#define UI_COLOR_BOX     0x0000u  // near-black panel behind text
#define UI_COLOR_TEXT    0xFFFFu  // white
#define UI_COLOR_DIM     0x9CF3u  // light grey, secondary lines
#define UI_COLOR_SELECT  0x04FFu  // highlight bar for the chosen row
#define UI_COLOR_SELECT_TEXT 0x0000u
#define UI_COLOR_RULE    0x4208u  // separator
#define UI_COLOR_BACKDROP 0x18E3u // status page background

// Fill a rectangle. Coordinates are screen-space; the band clips them.
void ui_text_fill(ui_surface_t surface, int x, int y, int w, int h, uint16_t color);

// Draw one line of text with an opaque background box, so it stays readable over
// moving pictures. Returns the x coordinate just past the text.
int ui_text_draw(ui_surface_t surface, int x, int y, const char *text,
                 uint16_t foreground, uint16_t background);

// Width in pixels the text would occupy, for centring and for truncation.
int ui_text_width(const char *text);

// Copy at most `limit` bytes of `text` into `out` without splitting a character,
// then terminate. Returns true when the whole string fitted.
bool ui_text_truncate(char *out, size_t out_size, const char *text, int limit);

// A bounded, immutable glyph layout. Prepare only when a menu changes; painting
// clips glyph rows before rasterization and performs no UTF-8 or font searches.
#define UI_TEXT_RUN_MAX 32u
typedef struct {
    uint16_t glyphs[UI_TEXT_RUN_MAX];
    uint16_t width;
    uint8_t count;
} ui_text_run_t;
void ui_text_prepare(ui_text_run_t *run, const char *text, unsigned max_width);
// Transparent glyphs: the caller has already painted the panel/card background.
void ui_text_paint(ui_surface_t surface, int x, int y, const ui_text_run_t *run,
                   uint16_t color);
