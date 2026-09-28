#include "solver.hpp"

namespace jacobi {

double dot_row(const Matrix& m, int row, const std::vector<double>& x) {
    double s = 0.0;
    for (int j = 0; j < m.n; j++) {
        if (j != row) {
            s += m.at(row, j) * x[j];
        }
    }
    return s;
}

std::vector<double> jacobi_sweep(const Matrix& m, const std::vector<double>& b,
                                 const std::vector<double>& x) {
    std::vector<double> next(m.n);
    for (int i = 0; i < m.n; i++) {
        next[i] = (b[i] - dot_row(m, i, x)) / m.at(i, i);
    }
    return next;
}

std::vector<double> solve(const Matrix& m, const std::vector<double>& b, int iters) {
    std::vector<double> x(m.n, 0.0);
    for (int k = 0; k < iters; k++) {
        x = jacobi_sweep(m, b, x);
    }
    return x;
}

}  // namespace jacobi
