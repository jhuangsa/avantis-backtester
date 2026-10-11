#pragma once
#include <algorithm>
#include <cmath>
#include <concepts>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

#include "avbt/costs.hpp"
#include "avbt/indicators.hpp"
#include "avbt/markets.hpp"
#include "avbt/portfolio.hpp"
#include "avbt/version.hpp"

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
    // 0.03 trails the stop 3% of the fill behind the best price; 0 for none.
    double trail_distance = 0.0;
    // Fraction of the position the take profit closes. ADR 0014.
    double take_profit_fraction = 1.0;
};

// One closed trade, with the clock steps of its fills. A step indexes
// Result::clock, not one market's bars; on one market they are the same.
struct Trade {
    std::string instrument;
    int entry_bar = 0;
    int exit_bar = 0;
    // UTC seconds of the two fill steps, clock[entry_bar] and clock[exit_bar].
    int64_t entry_time = 0;
    int64_t exit_time = 0;
    Side side = Side::Long;
    double entry_price = 0.0;
    double exit_price = 0.0;
    double size = 0.0;
    double leverage = 1.0;
    // Open plus close fee, and holding costs, in account money.
    double fees = 0.0;
    double holding_costs = 0.0;
    // Account money, after fees and holding costs.
    double result = 0.0;
    Cause cause = Cause::Order;
};

struct Result {
    std::vector<Trade> trades;
    // Equity at the close of every clock step. Open positions are marked at
    // the close: a mark, not a fill, so no fee.
    std::vector<double> equity;
    // Realized money at the end; positions still open are not in it.
    double ending_balance = 0.0;
    // Positions still open after the last step: not trades, marked in the
    // last equity value only.
    std::vector<Position> open_positions;
    // The base timeframe the run stepped on.
    Timeframe timeframe = Timeframe::Min1;
    // The UTC second at which each step opens; Markets::clock().
    std::vector<int64_t> clock;
    // The engine version that made this result.
    std::string version = avbt::version;
};

// What every strategy has: prepare resets its lines and computes them from
// the markets; update extends them with only the bars added since the last
// call, so a live caller can append bars and update; decide is called at the close of every clock step with `now`, the
// UTC second of that close, and returns orders. A strategy reads its bars
// through last_closed(bars, now), which never returns a bar still open. No
// base class, no virtual call.
// Checks the settings and hands state_delay to a strategy that reads labels.
// Throws std::invalid_argument unless starting_balance > 0, risk_per_trade
// and hard_stop are in (0, 1], and state_delay is whole minutes, at least 60.
template <class S>
void use_settings(S& s, const PortfolioSettings& p) {
    if (!(p.starting_balance > 0) || std::isinf(p.starting_balance)) {
        throw std::invalid_argument("starting_balance must be above 0, got " + std::to_string(p.starting_balance));
    }
    if (!(p.risk_per_trade > 0 && p.risk_per_trade <= 1)) {
        throw std::invalid_argument("risk_per_trade must be in (0, 1], got " + std::to_string(p.risk_per_trade));
    }
    if (!(p.hard_stop > 0 && p.hard_stop <= 1)) {
        throw std::invalid_argument("hard_stop must be in (0, 1], got " + std::to_string(p.hard_stop));
    }
    if (p.state_delay < 60 || p.state_delay % 60 != 0) {
        throw std::invalid_argument("state_delay must be a whole number of minutes, at least 60, got " +
                                    std::to_string(p.state_delay));
    }
    if constexpr (requires { s.state_delay; }) s.state_delay = p.state_delay;
    if constexpr (requires { s.a; s.b; }) use_settings(s.a, p), use_settings(s.b, p);
}

template <class S>
concept Strategy = requires(S s, const Markets& markets, int64_t now, const Report& report,
                            const std::vector<Position>& positions) {
    s.prepare(markets);
    s.update(markets);
    { s.decide(now, report, positions) } -> std::same_as<std::vector<Order>>;
};

// Runs one strategy on several markets on one clock (Markets::clock). On
// each step t:
// 1. Fill the orders from step t-1, closes before opens so freed collateral
//    can pay for a new trade, each at the open of its own market's base bar.
//    An order for a market with no bar at step t (its data has not started,
//    or has ended) is dropped.
// 2. Check every position's levels on its own market's base bar.
// 3. Close the position of a market whose data ends at this step, at its
//    last close, unless this is the run's last step.
// 4. Add each open position's holding cost for its base bar.
// 5. Record equity, then ask the strategy.
// Orders returned on the last step never fill. The caller chooses the bars;
// nothing is trimmed. Every market needs Costs; check_costs. ADR 0010, 0011, 0016.
// on_step, if given, sees every step's positions and the strategy's orders.
struct NoStep {
    void operator()(int, const std::vector<Position>&, const std::vector<Order>&) const {}
};
template <Strategy S, class OnStep = NoStep>
Result backtest(S& strategy, const Markets& markets, const MarketCosts& costs,
                PortfolioSettings settings, OnStep on_step = {}) {
    check_costs(markets, costs);
    use_settings(strategy, settings);
    Portfolio portfolio(settings);
    Result result{.timeframe = markets.timeframe(), .clock = markets.clock()};
    // The portfolio does not know clock steps, so the loop keeps them.
    std::map<std::string, int> entry_bar;
    std::vector<Order> waiting;

    auto record = [&](const Closed& c, int t) {
        int e = entry_bar[c.instrument];
        result.trades.push_back(Trade{c.instrument, e, t, result.clock[e], result.clock[t], c.side,
                                      c.entry_price, c.exit_price, c.size, c.leverage, c.fees,
                                      c.holding, c.result, c.cause});
        // A partial close leaves the rest of the trade open, with its entry bar.
        bool still_open = std::any_of(portfolio.positions().begin(), portfolio.positions().end(),
                                      [&](const Position& p) { return p.instrument == c.instrument; });
        if (!still_open) entry_bar.erase(c.instrument);
    };

    strategy.prepare(markets);
    const std::vector<int64_t>& clock = markets.clock();
    int n = static_cast<int>(clock.size());
    for (int t = 0; t < n; ++t) {
        std::stable_partition(waiting.begin(), waiting.end(),
                              [](const Order& o) { return o.kind == Order::Kind::Close; });
        for (const Order& o : waiting) {
            int i = markets.bar_at(o.instrument, t);
            if (i < 0) continue;
            double open = markets.base(o.instrument).open[i];
            if (o.kind == Order::Kind::Close) {
                if (auto c = portfolio.close(o.instrument, open)) record(*c, t);
                continue;
            }
            double sign = o.side == Side::Long ? 1.0 : -1.0;
            double stop = open * (1.0 - sign * o.stop_distance);
            double take = open * (1.0 + sign * o.take_profit_distance);
            // A refused open (no free cash, halted, a position already) records nothing.
            const Costs& cost = costs.at(o.instrument);
            if (portfolio.open(o.instrument, o.side, open, stop, o.leverage, take,
                               Fees{cost.open_fee, cost.close_fee},
                               o.trail_distance * open, o.take_profit_fraction, clock[t])) {
                entry_bar[o.instrument] = t;
            }
        }
        waiting.clear();

        std::vector<Quote> quotes;
        for (const Position& p : portfolio.positions()) {
            int i = markets.bar_at(p.instrument, t);
            if (i < 0) continue;
            const Bars& b = markets.base(p.instrument);
            quotes.push_back(Quote{p.instrument, b.open[i], b.high[i], b.low[i], b.close[i]});
        }
        for (const Closed& c : portfolio.check(quotes)) record(c, t);

        if (t + 1 < n) {
            for (const Market& m : markets.all()) {
                const Bars& b = m.timeframes.front();
                int i = markets.bar_at(m.instrument, t);
                bool ends = i == static_cast<int>(b.ts.size()) - 1;
                if (!ends) continue;
                if (auto c = portfolio.close(m.instrument, b.close[i], 1.0, Cause::EndOfData)) {
                    record(*c, t);
                }
            }
        }

        for (const Position& p : portfolio.positions()) {
            int i = markets.bar_at(p.instrument, t);
            if (i < 0) continue;
            const Costs& cost = costs.at(p.instrument);
            const auto& hold = p.side == Side::Long ? cost.hold_long : cost.hold_short;
            if (!hold.empty()) portfolio.hold(p.instrument, hold[i], markets.base(p.instrument).close[i]);
        }

        result.equity.push_back(portfolio.report().equity);
        int64_t now = clock[t] + seconds(markets.timeframe());
        waiting = strategy.decide(now, portfolio.report(), portfolio.positions());
        on_step(t, portfolio.positions(), waiting);
    }
    result.ending_balance = portfolio.report().balance;
    result.open_positions = portfolio.positions();
    return result;
}

}
