// Stats fixture: a renamed copy of smooth_series and an empty catch.
#include <vector>

double moving_average(const double* values, double* out, int count, double factor) {
    double total = 0.0;
    for (int x = 1; x < count - 1; ++x) {
        double before = values[x - 1] * 0.25;
        double here = values[x] * 0.5;
        double after = values[x + 1] * 0.25;
        double value = (before + here + after) * factor;
        if (value > 1.0) {
            value = 1.0;
        }
        out[x] = value;
        total += value;
    }
    return total;
}

void risky() {
    try {
        throw 1;
    } catch (...) {}
}
