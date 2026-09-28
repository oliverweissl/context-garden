#pragma once
#include "matrix.hpp"

namespace jacobi {
std::vector<double> solve(const Matrix& m, const std::vector<double>& b, int iters);
}
