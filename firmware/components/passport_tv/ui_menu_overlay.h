#pragma once
#include "ui_menu.h"
#include "ui_text.h"

#define UI_MENU_OVERLAY_ROWS 3u
// Font references only (under 1 KiB), not a second RGB frame or heap allocation.
// Owned exclusively by the video task. A new list generation invalidates names.
typedef struct {
    bool valid, quality_enabled;
    ui_view_t view;
    unsigned selected, scroll, quality, quality_selected, count;
    uint32_t revision, builds;
    ui_text_run_t text[12];
} ui_menu_overlay_t;
void ui_menu_overlay_paint(ui_menu_overlay_t *cache, const ui_menu_t *menu,
                           ui_surface_t surface);
// Freeze the menu for a complete display frame. A close/open gesture cannot
// invalidate an occlusion decision partway through the same frame.
void ui_menu_overlay_begin(ui_menu_overlay_t *cache, const ui_menu_t *menu);
void ui_menu_overlay_draw(const ui_menu_overlay_t *cache, ui_surface_t surface);
bool ui_menu_overlay_covers(const ui_menu_overlay_t *cache, unsigned y, unsigned rows);
