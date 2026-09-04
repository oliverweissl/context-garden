#include "solver.hpp"
#include "linalg.hpp"

double norm(const Matrix& m) {
    double total = 0.0;
    for (int i = 0; i < m.rows * m.cols; i++) {
        total += m.data[i] * m.data[i];
    }
    return total;
}

double residual(const Matrix& m) {
    return norm(m);
}

bool converge(Matrix& m, double tolerance) {
    double r = residual(m);
    if (r < tolerance) {
        return true;
    }
    return false;
}
