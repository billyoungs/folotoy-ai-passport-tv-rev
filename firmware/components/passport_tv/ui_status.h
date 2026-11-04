#pragma once
#include "ui_text.h"

#define UI_STATUS_WIFI_W 19
#define UI_STATUS_WIFI_H 15
#define UI_STATUS_BATTERY_ICON_W 24
#define UI_STATUS_BATTERY_ICON_H 11

// (x,y) is the top-left. 1..4 light the dot then three arcs; 0 is
// disconnected/unknown with a slash; values above 4 clamp to full strength.
void ui_status_wifi(ui_surface_t surface, int x, int y, unsigned bars, uint16_t bg);

// Right is exclusive, including text. Text top = center_y - UI_TEXT_LINE_H/2.
// Negative SOC is neutral "--"; >100 clamps to 100. Clears the maximum group
// width (icon + 5 + ui_text_width("100%")), so 1 Hz redraws erase old values.
void ui_status_battery(ui_surface_t surface, int right, int center_y, int soc,
                       uint16_t bg);
