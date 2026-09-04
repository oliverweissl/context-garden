#pragma once
#include "linalg.hpp"

double residual(const Matrix& m);
bool converge(Matrix& m, double tolerance);
