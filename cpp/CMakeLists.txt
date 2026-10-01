cmake_minimum_required(VERSION 3.20)
project(avbt LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Optimize unless the caller asks for another build type.
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

add_library(avbt src/indicators.cpp src/portfolio.cpp src/markets.cpp)
target_include_directories(avbt PUBLIC include)
target_compile_options(avbt PRIVATE -Wall -Wextra)
target_compile_definitions(avbt PRIVATE _LIBCPP_ENABLE_ASSERTIONS=1)

add_executable(test_indicators tests/test_indicators.cpp)
target_link_libraries(test_indicators PRIVATE avbt)
target_compile_options(test_indicators PRIVATE -Wall -Wextra)
target_compile_definitions(test_indicators PRIVATE _LIBCPP_ENABLE_ASSERTIONS=1)

add_executable(test_portfolio tests/test_portfolio.cpp)
target_link_libraries(test_portfolio PRIVATE avbt)
target_compile_options(test_portfolio PRIVATE -Wall -Wextra)
target_compile_definitions(test_portfolio PRIVATE _LIBCPP_ENABLE_ASSERTIONS=1)

add_executable(test_backtest tests/test_backtest.cpp)
target_link_libraries(test_backtest PRIVATE avbt)
target_compile_options(test_backtest PRIVATE -Wall -Wextra)
target_compile_definitions(test_backtest PRIVATE _LIBCPP_ENABLE_ASSERTIONS=1)

add_executable(test_strategies tests/test_strategies.cpp)
target_link_libraries(test_strategies PRIVATE avbt)
target_compile_options(test_strategies PRIVATE -Wall -Wextra)
target_compile_definitions(test_strategies PRIVATE _LIBCPP_ENABLE_ASSERTIONS=1)

add_executable(test_states tests/test_states.cpp)
target_link_libraries(test_states PRIVATE avbt)
target_compile_options(test_states PRIVATE -Wall -Wextra)
target_compile_definitions(test_states PRIVATE _LIBCPP_ENABLE_ASSERTIONS=1)

add_executable(test_optimize tests/test_optimize.cpp)
target_link_libraries(test_optimize PRIVATE avbt)
target_compile_options(test_optimize PRIVATE -Wall -Wextra)
target_compile_definitions(test_optimize PRIVATE _LIBCPP_ENABLE_ASSERTIONS=1)

add_executable(test_run tests/test_run.cpp)
target_link_libraries(test_run PRIVATE avbt)
target_compile_options(test_run PRIVATE -Wall -Wextra)
target_compile_definitions(test_run PRIVATE _LIBCPP_ENABLE_ASSERTIONS=1)

# Python module avbt_cpp, built only when pybind11 is found.
# Point CMake at it with -Dpybind11_DIR=$(python3 -m pybind11 --cmakedir).
find_package(pybind11 CONFIG QUIET)
if(pybind11_FOUND)
    set_target_properties(avbt PROPERTIES POSITION_INDEPENDENT_CODE ON)
    pybind11_add_module(avbt_cpp python/avbt_py.cpp)
    target_link_libraries(avbt_cpp PRIVATE avbt)
endif()

enable_testing()
add_test(NAME test_indicators COMMAND test_indicators)
add_test(NAME test_portfolio COMMAND test_portfolio)
add_test(NAME test_backtest COMMAND test_backtest)
add_test(NAME test_strategies COMMAND test_strategies)
add_test(NAME test_states COMMAND test_states)
add_test(NAME test_optimize COMMAND test_optimize)
add_test(NAME test_run COMMAND test_run)
