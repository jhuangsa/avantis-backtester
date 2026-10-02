// Tests for weekend_breakout_fade on hand-built hourly bars. Returns 0 when every check passes.

#include "avbt/library/weekend_breakout_fade.hpp"

#include <cstdio>
#include <string>
#include <vector>

namespace {

int failures = 0;
void check(const std::string& label, bool ok) {
    if (!ok) ++failures, std::printf("FAIL %s\n", label.c_str());
}

using namespace avbt;

Bars bars(const std::vector<double>& c) {
    Bars b;
    b.timeframe = Timeframe::Hour1;
    for (size_t i = 0; i < c.size(); ++i) {
        b.ts.push_back(static_cast<int64_t>(i) * 3600);
        b.open.push_back(i ? c[i - 1] : c[0]);
        b.high.push_back(std::max(c[i], b.open.back()));
        b.low.push_back(std::min(c[i], b.open.back()));
        b.close.push_back(c[i]);
        b.minutes_with_data.push_back(60);
    }
    return b;
}

}  // namespace

int main() {
    // A range of 99 to 101, a break to 103 at bar 20, back to 100 at bar 21, then lower.
    const size_t n = 40;
    std::vector<double> c;
    for (size_t i = 0; i < n; ++i) c.push_back(i < 20 ? (i % 2 ? 101.0 : 99.0) : i == 20 ? 103 : i == 21 ? 100 : 98);
    Markets m = Markets::make({Market{"ETH", {bars(c)}}});

    WeekendBreakoutFade s;
    s.params.instrument = "ETH";
    s.params.timeframe = Timeframe::Hour1;
    s.params.window = 10;
    s.prepare(m);
    check("no order on undefined lines", s.decide(5 * 3600, Report{}, {}).empty());

    Result r = backtest(s, m, MarketCosts{{"ETH", Costs{}}}, PortfolioSettings{});
    check("it trades", !r.trades.empty());
    if (r.trades.empty()) return 1;
    const Trade& t = r.trades[0];
    check("short after the failed break up", t.side == Side::Short);
    // Bar 21 closes back inside at 22:00; the fill is the open of bar 22.
    check("fills next open", t.entry_bar == 22 && t.entry_price == c[21]);
    if (failures == 0) std::printf("all weekend_breakout_fade checks passed\n");
    return failures == 0 ? 0 : 1;
}
