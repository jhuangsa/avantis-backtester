// Tests for false_break_1h on synthetic bars with sharp jumps: it trades,
// never opens on an undefined operand, fills at the next open, and keeps to
// max_open. Returns 0 when every check passes.

#include "avbt/library.hpp"

#include <cmath>
#include <cstdio>
#include <functional>
#include <string>

using namespace avbt;

namespace {

int failures = 0;
void check_true(const std::string& label, bool ok) {
    if (!ok) ++failures, std::printf("FAIL %s\n", label.c_str());
}

// 15-minute bars of price(i), with 1-hour and 4-hour bars made from them.
std::vector<Bars> make(std::size_t n, const std::function<double(std::size_t)>& price) {
    std::vector<Bars> out;
    for (Timeframe tf : {Timeframe::Min15, Timeframe::Hour1, Timeframe::Hour4}) {
        Bars b;
        b.timeframe = tf;
        std::size_t k = seconds(tf) / 900;
        for (std::size_t s = 0; s + k <= n; s += k) {
            double o = price(s), c = price(s + k - 1), h = std::max(o, c), l = std::min(o, c);
            for (std::size_t i = s; i < s + k; ++i) h = std::max(h, price(i)), l = std::min(l, price(i));
            b.ts.push_back(static_cast<int64_t>(s) * 900);
            b.open.push_back(o), b.high.push_back(h * 1.002), b.low.push_back(l * 0.998), b.close.push_back(c);
            b.minutes_with_data.push_back(static_cast<int>(k * 15));
        }
        out.push_back(b);
    }
    return out;
}

}  // namespace

bool registered(const std::string& name) {
    auto lib = library();
    return std::any_of(lib.begin(), lib.end(), [&](const StrategyInfo& x) { return x.name == name; });
}

bool next_open(const Result& r, const Markets& m) {
    for (const Trade& t : r.trades) {
        int i = m.bar_at(t.instrument, t.entry_bar);
        if (t.entry_bar == 0 || t.entry_price != m.base(t.instrument).open[i]) return false;
    }
    return !r.trades.empty();
}

int main() {
    std::size_t n = 16 * 400;
    auto jumpy = [](double phase) {
        return [=](std::size_t i) {
            double j = (i + std::size_t(phase * 50)) % 300 < 4 ? 1.05 : 1.0;
            return 100 * j * (1 + 0.01 * std::sin(i / 7.0 + phase)) * (1 + i * 1e-5);
        };
    };
    Markets m = Markets::make({{"A", make(n, jumpy(0))}, {"B", make(n, jumpy(1))}, {"C", make(n, jumpy(2))}});
    MarketCosts costs;
    for (const Market& x : m.all()) costs[x.instrument] = Costs{.open_fee = 0.0005, .close_fee = 0.0005};

    bool undefined_open = false;
    Result r;
    FalseBreak1h fb;
    fb.params.regime = 0;
    fb.params.max_open = 1;
    bool over = false;
    r = backtest(fb, m, costs, PortfolioSettings{}, [&](int t, const std::vector<Position>& held,
                                                       const std::vector<Order>& orders) {
        int64_t now = m.clock()[t] + seconds(m.timeframe());
        int opens = 0;
        for (const Order& o : orders) {
            if (o.kind != Order::Kind::Open) continue;
            ++opens;
            for (const auto& l : fb.lines) {
                if (l.instrument != o.instrument) continue;
                int i = last_closed(*l.bars, now);
                if (i < 1 || !defined(l.atr[i]) || !defined(l.high[i - 1])) undefined_open = true;
            }
        }
        if (opens + static_cast<int>(held.size()) > fb.params.max_open) over = true;
    });
    check_true("false_break_1h trades", r.trades.size() > 4);
    check_true("false_break_1h no open on an undefined operand", !undefined_open);
    check_true("false_break_1h fills at the next open", next_open(r, m));
    check_true("false_break_1h keeps to max_open", !over);
    check_true("false_break_1h registered", registered("false_break_1h"));
    return failures == 0 ? 0 : 1;
}
