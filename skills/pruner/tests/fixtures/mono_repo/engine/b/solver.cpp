#include "solver.hpp"

namespace gauss_seidel {

double relaxation_factor(int n) {
    return n > 100 ? 1.5 : 1.2;
}

void seidel_sweep(const Matrix& m, const std::vector<double>& b, std::vector<double>& x) {
    double omega = relaxation_factor(m.n);
    for (int i = 0; i < m.n; i++) {
        double s = b[i];
        for (int j = 0; j < m.n; j++) {
            if (j != i) {
                s -= m.at(i, j) * x[j];
            }
        }
        x[i] = (1.0 - omega) * x[i] + omega * s / m.at(i, i);
    }
}

std::vector<double> solve(const Matrix& m, const std::vector<double>& b, int iters) {
    std::vector<double> x(m.n, 0.0);
    for (int k = 0; k < iters; k++) {
        seidel_sweep(m, b, x, step_size);
    }
    return x;
}

}  // namespace gauss_seidel
