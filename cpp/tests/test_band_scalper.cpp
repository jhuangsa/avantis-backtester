// Tests for band_scalper on synthetic 15-minute bars with a few spikes:
// it fades a spike, only on a Low or Normal label, never on an undefined
// operand, and fills at the next open. Exit 0 is a pass.

#include "avbt/library.hpp"

#include <cmath>
#include <cstdio>
#include <vector>

using namespace avbt;

int failures = 0;
void check(const char* label, bool ok) {
    if (!ok) { ++failures; std::printf("FAIL %s\n", label); }
}

int main() {
    const int n = 400, per = 900 / 60;
    Bars b;
    b.timeframe = Timeframe::Min15;
    double prev = 100;
    for (int i = 0; i < n; ++i) {
        double c = prev + (i % 2 ? 0.2 : -0.2);
        if (i % 40 == 25) c = prev * 1.05;
        if (i % 40 == 5) c = prev * 0.95;
        b.ts.push_back(int64_t(i) * 900);
        b.open.push_back(prev);
        b.high.push_back(std::max(prev, c) + 0.1);
        b.low.push_back(std::min(prev, c) - 0.1);
        b.close.push_back(c);
        b.minutes_with_data.push_back(per);
        prev = c;
    }
    // Labels: in each 40-bar block, bars 0-9 and 20-29 are Normal, the rest Shock.
    auto label = [&](int bar) { return bar % 20 < 10 ? VolatilityState::Normal : VolatilityState::Shock; };
    States st;
    for (int i = 0; i < n * per; ++i) {
        st.market.push_back(MarketState::Mixed);
        st.trend.push_back(TrendState::NonTrending);
        st.volatility.push_back(label(i / per));
    }
    Markets m = Markets::make({Market{"AVAX", {b}, st}});
    BandScalper s;
    s.params.timeframe = Timeframe::Min15;
    std::vector<int> open_steps;
    bool bad = false, wrong_side = false, calm_only = true;
    Result r = backtest(s, m, {{"AVAX", Costs{}}}, PortfolioSettings{},
                        [&](int t, const std::vector<Position>&, const std::vector<Order>& orders) {
        for (const Order& o : orders) {
            if (o.kind != Order::Kind::Open) continue;
            open_steps.push_back(t);
            if (!defined(s.mean[t]) || !defined(s.atr[t])) bad = true;
            bool spike_up = b.close[t] > b.open[t];
            if ((o.side == Side::Long) == spike_up) wrong_side = true;
            VolatilityState v = label(t);
            if (v != VolatilityState::Normal && v != VolatilityState::Low) calm_only = false;
        }
    });
    check("it trades", r.trades.size() >= 4);
    check("no open on an undefined operand", !bad);
    check("fades the move", !wrong_side);
    check("no open on a Shock label", calm_only);
    bool next_open = !r.trades.empty();
    for (const Trade& x : r.trades) {
        int e = x.entry_bar;
        bool asked = false;
        for (int t : open_steps) asked = asked || t + 1 == e;
        next_open = next_open && asked && std::abs(x.entry_price - b.open[e]) < 1e-9;
    }
    check("fills at the next open", next_open);
    return failures == 0 ? 0 : 1;
}
