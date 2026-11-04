# Hosts may select a licensed font resource without forking the player.
if(NOT PASSPORT_TV_FONT_DIR)
    set(PASSPORT_TV_FONT_DIR "${CMAKE_CURRENT_LIST_DIR}")
endif()
idf_component_register(
    SRCS "tv_player.c" "av_frame_buffer.c" "av_protocol.c" "av_adpcm.c"
         "av_channel_policy.c" "av_settings.c" "ui_text.c" "ui_status.c" "ui_menu.c" "ui_menu_overlay.c"
    INCLUDE_DIRS "include" "${PASSPORT_TV_FONT_DIR}" "."
    EMBED_FILES "${PASSPORT_TV_FONT_DIR}/ui_font_cjk.bin"
    LDFRAGMENTS "tv_lwip_receive.lf"
    REQUIRES bsp esp_event esp_netif esp_timer esp_wifi nvs_flash esp_rom json lwip heap
)
# Preserve the accepted speed/RAM tradeoff when linked into either application.
set_source_files_properties(tv_player.c av_protocol.c av_adpcm.c av_frame_buffer.c
    ui_text.c ui_status.c ui_menu_overlay.c PROPERTIES COMPILE_OPTIONS "-O2")
foreach(tv_path_component lwip esp_lcd esp_driver_spi heap)
    idf_component_get_property(tv_path_lib ${tv_path_component} COMPONENT_LIB)
    target_compile_options(${tv_path_lib} PRIVATE -O2)
endforeach()
