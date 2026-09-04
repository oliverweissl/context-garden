#include "solver.hpp"
#include <cassert>

void test_converges_below_tolerance() {
    Matrix m;
    m.rows = 1;
    m.cols = 1;
    m.data[0] = 1e-9;
    assert(converge(m, 1e-6) == true);
}

int main() {
    test_converges_below_tolerance();
    return 0;
}
