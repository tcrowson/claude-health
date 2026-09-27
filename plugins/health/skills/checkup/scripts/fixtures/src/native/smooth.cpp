// Smooth fixture: the renamed copy of moving_average.
double smooth_series(const double* in, double* res, int n, double k) {
    double acc = 0.0;
    for (int i = 1; i < n - 1; ++i) {
        double a = in[i - 1] * 0.25;
        double b = in[i] * 0.5;
        double c = in[i + 1] * 0.25;
        double v = (a + b + c) * k;
        if (v > 1.0) {
            v = 1.0;
        }
        res[i] = v;
        acc += v;
    }
    return acc;
}
