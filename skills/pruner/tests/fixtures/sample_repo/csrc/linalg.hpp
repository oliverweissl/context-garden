#pragma once

struct Matrix {
    double data[4];
    int rows;
    int cols;
};

double norm(const Matrix& m);
