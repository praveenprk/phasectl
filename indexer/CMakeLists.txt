cmake_minimum_required(VERSION 3.16)
project(indexer LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
    pkg_check_modules(SQLite3 QUIET IMPORTED_TARGET sqlite3)
endif()

add_executable(indexer src/main.cpp)

if(TARGET PkgConfig::SQLite3)
    target_link_libraries(indexer PRIVATE PkgConfig::SQLite3)
else()
    find_package(SQLite3 REQUIRED)
    target_link_libraries(indexer PRIVATE SQLite::SQLite3)
endif()
