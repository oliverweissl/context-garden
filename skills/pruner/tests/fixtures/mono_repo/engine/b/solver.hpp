#pragma once
#include "matrix.hpp"

namespace gauss_seidel {
std::vector<double> solve(const Matrix& m, const std::vector<double>& b, int iters);
double relaxation_factor(int n);
}
