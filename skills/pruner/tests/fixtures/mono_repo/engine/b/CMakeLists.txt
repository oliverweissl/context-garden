add_library(solver_b solver.cpp)
target_include_directories(solver_b PUBLIC ${CMAKE_CURRENT_SOURCE_DIR} ../common)
target_compile_options(solver_b PRIVATE -O3)
