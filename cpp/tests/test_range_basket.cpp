// Tests for range_basket on two synthetic hourly markets that spike out of
// a range and come back: it trades both markets and both sides, only on a
// ranging label, never on an undefined operand, fades the spike, and fills
// at the next open. Exit 0 is a pass.

#include "avbt/library.hpp"

#include <cmath>
#include <cstdio>
#include <vector>

using namespace avbt;

int failures = 0;
void check(const char* label, bool ok) {
    if (!ok) { ++failures; std::printf("FAIL %s\n", label); }
}

Bars make(int n, int shift) {
    Bars b;
    b.timeframe = Timeframe::Hour1;
    double prev = 100;
    for (int i = 0; i < n; ++i) {
        double c = 100 + std::sin(i / 3.0);
        int k = (i + shift) % 30;
        if (k == 10) c = 104;
        if (k == 25) c = 96;
        b.ts.push_back(int64_t(i) * 3600);
        b.open.push_back(prev);
        b.high.push_back(std::max(prev, c) + 0.1);
        b.low.push_back(std::min(prev, c) - 0.1);
        b.close.push_back(c);
        b.minutes_with_data.push_back(60);
        prev = c;
    }
    return b;
}

int main() {
    const int n = 600;
    // Consolidation in the first half, TrendingUp in the second.
    States st;
    for (int i = 0; i < n * 60; ++i) {
        st.market.push_back(i < n * 30 ? MarketState::Consolidation : MarketState::TrendingUp);
        st.trend.push_back(TrendState::NonTrending);
        st.volatility.push_back(VolatilityState::Normal);
    }
    Markets m = Markets::make({Market{"A", {make(n, 0)}, st}, Market{"B", {make(n, 7)}, st}});
    RangeBasket s;
    s.params.window = 20;
    std::vector<int> open_steps;
    bool bad = false, wrong_side = false;
    Result r = backtest(s, m, {{"A", Costs{}}, {"B", Costs{}}}, PortfolioSettings{},
                        [&](int t, const std::vector<Position>&, const std::vector<Order>& orders) {
        for (const Order& o : orders) {
            if (o.kind != Order::Kind::Open) continue;
            open_steps.push_back(t);
            const auto& l = *std::find_if(s.lines.begin(), s.lines.end(),
                                          [&](const auto& x) { return x.instrument == o.instrument; });
            if (t < 1 || !defined(l.high[t]) || !defined(l.high[t - 1]) || !defined(l.atr[t])) { bad = true; continue; }
            double mid = (l.high[t] + l.low[t]) / 2;
            if ((o.side == Side::Short) != (l.bars->close[t] > mid)) wrong_side = true;
        }
    });
    check("it trades", r.trades.size() >= 4);
    check("no open on an undefined operand", !bad);
    check("fades the spike", !wrong_side);
    bool ranging_only = true, longs = false, shorts = false, a = false, b = false;
    for (int t : open_steps) ranging_only = ranging_only && t < n / 2;
    for (const Trade& x : r.trades) (x.side == Side::Long ? longs : shorts) = true, (x.instrument == "A" ? a : b) = true;
    check("no open off a ranging label", ranging_only);
    check("both sides", longs && shorts);
    check("both markets", a && b);
    bool next_open = !r.trades.empty();
    for (const Trade& x : r.trades) {
        bool asked = false;
        for (int t : open_steps) asked = asked || t + 1 == x.entry_bar;
        next_open = next_open && asked && std::abs(x.entry_price - m.base(x.instrument).open[x.entry_bar]) < 1e-9;
    }
    check("fills at the next open", next_open);
    return failures == 0 ? 0 : 1;
}
