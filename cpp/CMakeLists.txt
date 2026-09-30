cmake_minimum_required(VERSION 3.20)
project(avbt LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(avbt src/indicators.cpp)
target_include_directories(avbt PUBLIC include)
target_compile_options(avbt PRIVATE -Wall -Wextra)
target_compile_definitions(avbt PRIVATE _LIBCPP_ENABLE_ASSERTIONS=1)

add_executable(test_indicators tests/test_indicators.cpp)
target_link_libraries(test_indicators PRIVATE avbt)
target_compile_options(test_indicators PRIVATE -Wall -Wextra)
target_compile_definitions(test_indicators PRIVATE _LIBCPP_ENABLE_ASSERTIONS=1)

enable_testing()
add_test(NAME test_indicators COMMAND test_indicators)
