cmake_minimum_required(VERSION 3.16)
# In-tree application and exported standalone repository use the same components.
if(NOT EXISTS "${CMAKE_CURRENT_LIST_DIR}/components/passport_tv")
    set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../../components/passport_wifi"
                             "${CMAKE_CURRENT_LIST_DIR}/../../../components/passport_tv"
                             "${CMAKE_CURRENT_LIST_DIR}/../../../components/bsp")
endif()
if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/../fonts/ui_font_data.h")
    set(PASSPORT_TV_FONT_DIR "${CMAKE_CURRENT_LIST_DIR}/../fonts")
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# 版本号作为合并固件文件名的版本来源（见 tools/build.sh 的版本化输出）。
project(FoloToy-AI-Passport VERSION 1.0.7)
