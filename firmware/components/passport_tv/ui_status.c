#include "ui_status.h"
#include <stdio.h>

#define SILVER 0xDEFBu
#define DIM 0x630Cu
#define AMBER 0xFE28u
#define RED 0xFA69u

// Sixteen integer samples per pixel; blend against the caller's opaque bar.
static uint16_t blend(uint16_t fg, uint16_t bg, unsigned coverage)
{
    unsigned inv = 16 - coverage;
    unsigned r = (((fg >> 11) * coverage + (bg >> 11) * inv + 8) / 16);
    unsigned g = ((((fg >> 5) & 63) * coverage + ((bg >> 5) & 63) * inv + 8) / 16);
    unsigned b = (((fg & 31) * coverage + (bg & 31) * inv + 8) / 16);
    return (uint16_t)((r << 11) | (g << 5) | b);
}

void ui_status_wifi(ui_surface_t s, int x, int y, unsigned bars, uint16_t bg)
{
    if (!s.pixels || s.width <= 0 || s.rows <= 0) return;
    if (bars > 4) bars = 4;
    for (int py = 0; py < UI_STATUS_WIFI_H; py++) {
        if (y + py < s.origin_y || y + py >= s.origin_y + s.rows) continue;
        for (int px = 0; px < UI_STATUS_WIFI_W; px++) {
            unsigned lit = 0, dim = 0;
            for (int sy = 1; sy < 8; sy += 2) for (int sx = 1; sx < 8; sx += 2) {
                int xx = px * 8 + sx, yy = py * 8 + sy;
                int dx = xx - 76, dy = yy - 104, d = dx * dx + dy * dy;
                unsigned level = d <= 100 ? 1 : 0;
                if (dy < 0 && dx <= -dy && -dx <= -dy) {
                    if (d >= 28*28 && d <= 40*40) level = 2;
                    if (d >= 56*56 && d <= 68*68) level = 3;
                    if (d >= 84*84 && d <= 96*96) level = 4;
                }
                // A background halo keeps the disconnected slash legible.
                int slash = xx + yy - 136;
                if (!bars && xx >= 28 && xx <= 124 && yy >= 12 && yy <= 108 &&
                    slash >= -13 && slash <= 13) {
                    if (slash >= -5 && slash <= 5) lit++;
                } else if (level) {
                    if (level <= bars) lit++;
                    else dim++;
                }
            }
            unsigned total = lit + dim;
            uint16_t ink = total ? blend(UI_COLOR_TEXT, DIM, (lit * 16 + total / 2) / total) : DIM;
            uint16_t color = blend(ink, bg, total);
            ui_text_fill(s, x + px, y + py, 1, 1, color);
        }
    }
}

// Rounded rectangle in eighth-pixel units, used for outline and fill coverage.
static bool rounded(int x, int y, int w, int h, int r)
{
    if (x < 0 || y < 0 || x >= w || y >= h) return false;
    int dx = x < r ? r - x : x > w - r ? x - (w - r) : 0;
    int dy = y < r ? r - y : y > h - r ? y - (h - r) : 0;
    return dx * dx + dy * dy <= r * r;
}

void ui_status_battery(ui_surface_t s, int right, int center_y, int soc, uint16_t bg)
{
    if (!s.pixels || s.width <= 0 || s.rows <= 0 || right <= 0) return;
    char text[5];
    if (soc > 100) soc = 100;
    // Centered dash glyphs overhang their advances; a space retains both dashes.
    if (soc < 0) snprintf(text, sizeof text, "-- ");
    else snprintf(text, sizeof text, "%d%%", soc);
    uint16_t color = soc < 0 ? UI_COLOR_DIM : soc <= 10 ? RED : soc <= 20 ? AMBER : SILVER;
    int text_x = right - ui_text_width(text);
    int x = text_x - 5 - UI_STATUS_BATTERY_ICON_W;
    int y = center_y - UI_STATUS_BATTERY_ICON_H / 2;
    int text_y = center_y - (int)UI_TEXT_LINE_H / 2;
    int width = UI_STATUS_BATTERY_ICON_W + 5 + ui_text_width("100%");
    ui_text_fill(s, right - width, text_y, width, UI_TEXT_LINE_H, bg);
    for (int py = 0; py < UI_STATUS_BATTERY_ICON_H; py++) {
        if (y + py < s.origin_y || y + py >= s.origin_y + s.rows) continue;
        for (int px = 0; px < UI_STATUS_BATTERY_ICON_W; px++) {
            unsigned coverage = 0;
            for (int sy = 1; sy < 8; sy += 2) for (int sx = 1; sx < 8; sx += 2) {
                int xx = px * 8 + sx, yy = py * 8 + sy;
                bool outline = rounded(xx, yy, 176, 88, 20) &&
                               !rounded(xx - 8, yy - 8, 160, 72, 12);
                bool terminal = rounded(xx - 176, yy - 28, 16, 32, 6);
                bool fill = soc >= 0 && xx < 16 + soc * 144 / 100 &&
                            rounded(xx - 16, yy - 16, 144, 56, 8);
                bool unknown = soc < 0 && xx >= 64 && xx < 112 && yy >= 40 && yy < 48;
                if (outline || terminal || fill || unknown) coverage++;
            }
            ui_text_fill(s, x + px, y + py, 1, 1, blend(color, bg, coverage));
        }
    }
    ui_text_run_t run;
    ui_text_prepare(&run, text, (unsigned)ui_text_width(text));
    // One-row views clip glyph overhang at right without changing row stride.
    for (int row = 0; row < (int)UI_TEXT_LINE_H; row++) {
        int yy = text_y + row;
        if (yy < s.origin_y || yy >= s.origin_y + s.rows) continue;
        ui_surface_t line = {s.pixels + (size_t)(yy - s.origin_y) * s.width * 2,
                             right < s.width ? right : s.width, yy, 1};
        ui_text_paint(line, text_x, text_y, &run, color);
    }
}
