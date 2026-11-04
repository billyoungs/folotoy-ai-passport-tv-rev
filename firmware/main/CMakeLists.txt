# Reuse the same setup-interface access policy as FoloTalk.
set(portal_access "${CMAKE_CURRENT_LIST_DIR}/folotalk_portal_access.c")
if(NOT EXISTS "${portal_access}")
    set(portal_access "${CMAKE_CURRENT_LIST_DIR}/../../../../main/folotalk_portal_access.c")
endif()
get_filename_component(portal_access_include "${portal_access}" DIRECTORY)
idf_component_register(SRCS "${CMAKE_CURRENT_LIST_DIR}/passport_tv_main.c"
    "${CMAKE_CURRENT_LIST_DIR}/tv_config.c" "${CMAKE_CURRENT_LIST_DIR}/pairing_client.c" "${portal_access}"
    INCLUDE_DIRS "${CMAKE_CURRENT_LIST_DIR}" "${portal_access_include}"
    EMBED_TXTFILES "${CMAKE_CURRENT_LIST_DIR}/setup.html"
    REQUIRES passport_wifi passport_tv bsp esp_wifi esp_netif esp_event esp_http_server
             esp-tls esp_timer mbedtls nvs_flash json lwip http_parser)
