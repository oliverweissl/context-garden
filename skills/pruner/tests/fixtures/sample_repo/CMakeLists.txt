cmake_minimum_required(VERSION 3.20)
project(csolver)
add_executable(csolver csrc/solver.cpp)
add_executable(csolver_tests csrc/test_solver.cpp csrc/solver.cpp)
