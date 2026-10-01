// Each rolling indicator, updated bar by bar, equals its full-series function
// bit for bit (NaN equals NaN), across NaN gaps and windows longer than the series.
#include "avbt/indicators.hpp"
#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>

using namespace avbt;

static int failures = 0;

static bool same(double a, double b) {
    if (std::isnan(a) && std::isnan(b)) return true;
    return std::memcmp(&a, &b, sizeof a) == 0;
}

static void check(const char* what, const std::vector<double>& full, const std::vector<double>& rolled) {
    bool ok = full.size() == rolled.size();
    for (size_t i = 0; ok && i < full.size(); i++) ok = same(full[i], rolled[i]);
    if (!ok) { std::printf("FAIL %s\n", what); failures++; }
}

template <class F>
static std::vector<double> roll(size_t size, F f) {
    std::vector<double> out;
    for (size_t i = 0; i < size; i++) out.push_back(f(i));
    return out;
}

int main() {
    double nan = std::nan("");
    Bars b;
    b.timeframe = Timeframe::Min15;
    double close[] = {10, 10.3, 9.7, nan, 11.1, 10.9, 12.4, 12.0, 11.3, 0.7, 13.9, 14.2, 13.1, 12.8, 15.5, 15.1};
    for (int i = 0; i < 16; i++) {
        b.ts.push_back(1700000000 + 900 * i);
        b.close.push_back(close[i]);
        b.open.push_back(i == 5 ? nan : close[i] - 0.1 * (i % 3));
        b.high.push_back(i == 8 ? nan : close[i] + 0.37 * (i % 4 + 1));
        b.low.push_back(close[i] - 0.21 * (i % 5 + 1));
    }
    size_t size = b.ts.size();

    for (int n : {1, 2, 3, 5, 16, 20}) {
        Sma sm(n); PriorMax mx(n); PriorMin mn(n); PctChange pc(n); BarChange bc(n);
        Atr at(n); Chandelier cl(n, 2.5, Side::Long), cs(n, 1.5, Side::Short);
        check("sma", sma(b.close, n), roll(size, [&](size_t i) { return sm.update(b.close[i]); }));
        check("prior_max", prior_max(b.high, n), roll(size, [&](size_t i) { return mx.update(b.high[i]); }));
        check("prior_min", prior_min(b.low, n), roll(size, [&](size_t i) { return mn.update(b.low[i]); }));
        check("pct_change", pct_change(b.close, n), roll(size, [&](size_t i) { return pc.update(b.close[i]); }));
        check("bar_change", bar_change(b, Field::Close, Field::Open, n),
              roll(size, [&](size_t i) { return bc.update(b.close[i], b.open[i]); }));
        check("atr", atr(b, n), roll(size, [&](size_t i) { return at.update(b.high[i], b.low[i], b.close[i]); }));
        check("chandelier long", chandelier(b, n, 2.5, Side::Long),
              roll(size, [&](size_t i) { return cl.update(b.high[i], b.low[i], b.close[i]); }));
        check("chandelier short", chandelier(b, n, 1.5, Side::Short),
              roll(size, [&](size_t i) { return cs.update(b.high[i], b.low[i], b.close[i]); }));
    }
    TrueRange tr;
    check("true_range", true_range(b), roll(size, [&](size_t i) { return tr.update(b.high[i], b.low[i], b.close[i]); }));
    HourOfDay hd(b.timeframe);
    check("hour_of_day", hour_of_day(b), roll(size, [&](size_t i) { return hd.update(b.ts[i]); }));

    if (failures == 0) std::printf("all rolling tests passed\n");
    return failures == 0 ? 0 : 1;
}
