#pragma once
#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

#include "avbt/registry.hpp"

namespace avbt {

// Weekend basket: every market passed in, both sides, at most max_open
// positions at once. On each market: at the close of the bar ending at UTC
// hour `hour` on Monday, fade the move of the last `span` bars (take profit
// tp_share of the move, stop w_stops ATRs, out after w_hold bars). With
// breakouts, also fade a close beyond the prior `window` bars' high (low)
// that is back inside within `within` bars (take profit tp_atrs ATRs, stop
// b_stops ATRs, out after b_hold bars). A weekend crash on every market at
// once is the rare big loss.
struct WeekendBasket {
    struct Params {
        Timeframe timeframe = Timeframe::Hour4;
        int hour = 8, span = 12, w_hold = 12, window = 24, within = 4, b_hold = 8, atr_period = 20, max_open = 5;
        double tp_share = 0.3, w_stops = 1.75, tp_atrs = 1.2, b_stops = 3.0, leverage = 3.0;
        bool breakouts = true;
    } params;
    struct Lines {
        std::string instrument;
        const Bars* bars = nullptr;
        std::vector<double> high, low, atr;
        PriorMax high_of{1};
        PriorMin low_of{1};
        Atr atr_of{1};
        size_t done = 0;
        int seen_bar = -1, hold = 0;
    };
    std::vector<Lines> lines;

    bool trades(const std::string& instrument) const {
        return std::any_of(lines.begin(), lines.end(), [&](const Lines& l) { return l.instrument == instrument; });
    }
    void prepare(const Markets& m) {
        lines.clear();
        update(m);
    }
    void update(const Markets& m) {
        for (const Market& market : m.all()) {
            auto it = std::find_if(lines.begin(), lines.end(),
                                   [&](const Lines& l) { return l.instrument == market.instrument; });
            if (it == lines.end()) {
                lines.push_back(Lines{.instrument = market.instrument,
                                      .high_of = PriorMax(params.window), .low_of = PriorMin(params.window),
                                      .atr_of = Atr(params.atr_period)});
                it = lines.end() - 1;
            }
            Lines& l = *it;
            l.bars = &m.at(market.instrument, params.timeframe);
            const Bars& b = *l.bars;
            for (; l.done < b.ts.size(); ++l.done) {
                size_t i = l.done;
                l.high.push_back(l.high_of.update(b.high[i]));
                l.low.push_back(l.low_of.update(b.low[i]));
                l.atr.push_back(l.atr_of.update(b.high[i], b.low[i], b.close[i]));
            }
        }
    }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        const auto& p = params;
        std::vector<Order> orders;
        int open = static_cast<int>(positions.size());
        for (Lines& l : lines) {
            int t = last_closed(*l.bars, now);
            if (t < std::max(1, p.span) || t == l.seen_bar) continue;
            l.seen_bar = t;
            auto mine = std::find_if(positions.begin(), positions.end(),
                                     [&](const Position& x) { return x.instrument == l.instrument; });
            if (mine != positions.end()) {
                // hold is 0 after a restart; then the longer of the two is used.
                int h = l.hold ? l.hold : std::max(p.w_hold, p.b_hold);
                if (mine->entry_time != unknown_time && now - mine->entry_time >= h * seconds(p.timeframe))
                    orders.push_back(Order{.kind = Order::Kind::Close, .instrument = l.instrument});
                continue;
            }
            if (open >= p.max_open || !defined(l.atr[t])) continue;
            const auto& c = l.bars->close;
            double a = l.atr[t] / c[t];
            int64_t end = l.bars->ts[t] + seconds(p.timeframe);
            bool monday = (end / 86400 + 3) % 7 == 0 && (end / 3600) % 24 == p.hour;
            double move = c[t] - c[t - p.span];
            if (monday && move != 0) {
                l.hold = p.w_hold, ++open;
                orders.push_back(Order{.kind = Order::Kind::Open, .instrument = l.instrument,
                                       .side = move > 0 ? Side::Short : Side::Long, .stop_distance = p.w_stops * a,
                                       .take_profit_distance = p.tp_share * std::abs(move) / c[t], .leverage = p.leverage});
                continue;
            }
            if (!p.breakouts) continue;
            bool up = false, down = false;
            for (int j = t - 1; j >= std::max(0, t - p.within) && !up && !down; --j) {
                if (!defined(l.high[j]) || !defined(l.low[j])) break;
                up = c[j] > l.high[j] && c[t] < l.high[j] && c[t - 1] >= l.high[j];
                down = c[j] < l.low[j] && c[t] > l.low[j] && c[t - 1] <= l.low[j];
            }
            if (!up && !down) continue;
            l.hold = p.b_hold, ++open;
            orders.push_back(Order{.kind = Order::Kind::Open, .instrument = l.instrument,
                                   .side = up ? Side::Short : Side::Long, .stop_distance = p.b_stops * a,
                                   .take_profit_distance = p.tp_atrs * a, .leverage = p.leverage});
        }
        return orders;
    }
};
static_assert(Strategy<WeekendBasket>);

namespace detail {
inline StrategyInfo weekend_basket_info() {
    using P = WeekendBasket::Params;
    return single<WeekendBasket>("weekend_basket", {
        field("timeframe", &P::timeframe), field("hour", &P::hour, 0, 23), field("span", &P::span, 1, inf),
        field("w_hold", &P::w_hold, 1, inf), field("window", &P::window, 2, inf),
        field("within", &P::within, 1, inf), field("b_hold", &P::b_hold, 1, inf),
        field("atr_period", &P::atr_period, 1, inf), field("max_open", &P::max_open, 1, inf),
        field("tp_share", &P::tp_share, 0.0, inf), field("w_stops", &P::w_stops, 0.0, inf),
        field("tp_atrs", &P::tp_atrs, 0.0, inf), field("b_stops", &P::b_stops, 0.0, inf),
        field("leverage", &P::leverage, 0.0, 100.0), field("breakouts", &P::breakouts)},
        {Timeframe::Min15, Timeframe::Hour4});
}
}  // namespace detail

}
