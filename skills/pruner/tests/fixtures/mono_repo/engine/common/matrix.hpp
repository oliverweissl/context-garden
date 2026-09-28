#pragma once
#include <vector>

struct Matrix {
    int n;
    std::vector<double> data;
    double at(int i, int j) const { return data[i * n + j]; }
};

double dot_row(const Matrix& m, int row, const std::vector<double>& x);
