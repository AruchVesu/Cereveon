cmake_minimum_required(VERSION 3.22.1)

project("chessengine")

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ✅ Consolidated: Only use native_chess_engine.cpp
add_library(
        chessengine
        SHARED
        native_chess_engine.cpp
        SachmatuLenta.cpp
)

find_library(
        log-lib
        log
)

target_link_libraries(
        chessengine
        ${log-lib}
)
