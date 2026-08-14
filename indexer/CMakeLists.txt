cmake_minimum_required(VERSION 3.16)
project(phasedex LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
    pkg_check_modules(SQLite3 QUIET IMPORTED_TARGET sqlite3)
endif()

add_executable(phasedex src/main.cpp)

if(TARGET PkgConfig::SQLite3)
    target_link_libraries(phasedex PRIVATE PkgConfig::SQLite3)
else()
    find_package(SQLite3 REQUIRED)
    target_link_libraries(phasedex PRIVATE SQLite::SQLite3)
endif()

# "Static binary" here means what the plan (docs/internal/NATIVE_INDEXER_PLAN.md)
# actually promises: no Node/npm/interpreter runtime dependency, not a fully
# static executable including libc — that needs musl on Linux and isn't
# something Apple supports for system libraries on macOS at all. Statically
# linking the C++ runtime is the practical, portable middle ground: the
# result runs without requiring libstdc++/libc++ to be pre-installed at a
# matching version, while still dynamically linking libsqlite3 (which ships
# with the OS on macOS and is a near-universal package on Linux).
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU" OR CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
    if(NOT APPLE)
        target_link_options(phasedex PRIVATE -static-libgcc -static-libstdc++)
    endif()
endif()

include(GNUInstallDirs)
install(TARGETS phasedex RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
