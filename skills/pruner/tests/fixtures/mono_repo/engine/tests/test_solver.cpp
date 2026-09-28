#include <cassert>
#include <cmath>
#include "../a/solver.hpp"
#include "../b/solver.hpp"

void test_jacobi_identity() {
    Matrix m{2, {1.0, 0.0, 0.0, 1.0}};
    auto x = jacobi::solve(m, {3.0, 4.0}, 5);
    assert(std::fabs(x[0] - 3.0) < 1e-9);
}

void test_seidel_diagonal() {
    Matrix m{2, {2.0, 0.0, 0.0, 2.0}};
    auto x = gauss_seidel::solve(m, {2.0, 4.0}, 50);
    assert(std::fabs(x[1] - 2.0) < 1e-6);
}

int main() {
    test_jacobi_identity();
    test_seidel_diagonal();
    return 0;
}
