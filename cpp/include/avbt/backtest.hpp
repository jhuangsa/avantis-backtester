#pragma once
#include <cmath>
#include <concepts>
#include <map>
#include <string>
#include <vector>

#include "avbt/indicators.hpp"
#include "avbt/portfolio.hpp"

namespace avbt {

// False for NaN, the value an indicator holds before it has enough bars.
inline bool defined(double x) { return !std::isnan(x); }

// What a strategy returns at the close of bar t. It fills at the open of
// bar t+1. Distances are fractions of that open, because the strategy does
// not know it yet. See ADR 0009.
struct Order {
    enum class Kind { Open, Close };
    Kind kind = Kind::Open;
    std::string instrument;
    Side side = Side::Long;
    // 0.05 puts the stop 5% from the fill, on the losing side.
    double stop_distance = 0.0;
    // NaN for no take profit.
    double take_profit_distance = std::nan("");
    double leverage = 1.0;
    // Fraction of notional charged at every fill.
    double fee_rate = 0.0;
};

// One closed trade, with the bar indexes of its fills.
struct Trade {
    int entry_bar = 0;
    int exit_bar = 0;
    Side side = Side::Long;
    double entry_price = 0.0;
    double exit_price = 0.0;
    // Account money, after both fees.
    double result = 0.0;
    Cause cause = Cause::Order;
};

struct Result {
    std::vector<Trade> trades;
    // Equity at the close of every bar. Open positions are marked at the
    // close: a mark, not a fill, so no fee.
    std::vector<double> equity;
    // Realized money at the end; positions still open are not in it.
    double ending_balance = 0.0;
    int bar_size_seconds = 0;
};

// What every strategy has: prepare computes its lines once; decide reads
// bar t or earlier and returns orders. No base class, no virtual call.
template <class S>
concept Strategy = requires(S s, const Bars& bars, int t, const Report& report,
                            const std::vector<Position>& positions) {
    s.prepare(bars);
    { s.decide(t, report, positions) } -> std::same_as<std::vector<Order>>;
};

// Runs one strategy on one instrument's bars. On each bar t: fill the
// orders from bar t-1 at open[t], check levels on bar t (including the bar
// of the entry), record equity, then ask the strategy. Orders returned on
// the last bar never fill. The caller chooses the bars; nothing is trimmed.
template <Strategy S>
Result backtest(S& strategy, const Bars& bars, PortfolioSettings settings) {
    Portfolio portfolio(settings);
    Result result{.bar_size_seconds = bars.bar_size_seconds};
    // The portfolio does not know bar indexes, so the loop keeps them.
    std::map<std::string, int> entry_bar;
    std::vector<Order> waiting;

    auto record = [&](const Closed& c, int t) {
        result.trades.push_back(Trade{entry_bar[c.instrument], t, c.side, c.entry_price,
                                      c.exit_price, c.result, c.cause});
        entry_bar.erase(c.instrument);
    };

    strategy.prepare(bars);
    int n = static_cast<int>(bars.close.size());
    for (int t = 0; t < n; ++t) {
        double open = bars.open[t];
        for (const Order& o : waiting) {
            if (o.kind == Order::Kind::Close) {
                if (auto c = portfolio.close(o.instrument, open)) record(*c, t);
                continue;
            }
            double sign = o.side == Side::Long ? 1.0 : -1.0;
            double stop = open * (1.0 - sign * o.stop_distance);
            double take = open * (1.0 + sign * o.take_profit_distance);
            // A refused open (no free cash, halted, a position already) records nothing.
            if (portfolio.open(o.instrument, o.side, open, stop, o.leverage, take, o.fee_rate)) {
                entry_bar[o.instrument] = t;
            }
        }
        waiting.clear();

        std::vector<Quote> quotes;
        for (const Position& p : portfolio.positions()) {
            quotes.push_back(Quote{p.instrument, open, bars.high[t], bars.low[t], bars.close[t]});
        }
        for (const Closed& c : portfolio.check(quotes)) record(c, t);

        result.equity.push_back(portfolio.report().equity);
        waiting = strategy.decide(t, portfolio.report(), portfolio.positions());
    }
    result.ending_balance = portfolio.report().balance;
    return result;
}

}
