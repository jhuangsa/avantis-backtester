#pragma once
#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

#include "avbt/registry.hpp"

namespace avbt {

// Weekend and breakout fade: one instrument, both sides, two fades in one.
// Weekend: at the close of the bar ending at UTC hour `hour` on Monday, fade
// the move of the last `span` bars; take profit tp_share of the move, stop
// w_stops ATRs (w_atr period), out after w_hold bars, w_leverage.
// Breakout: a close above the prior `window` bars' high (below the low)
// that is back inside within `within` bars is faded; take profit tp_atrs
// ATRs, stop b_stops ATRs, out after b_hold bars, b_leverage.
// One position at a time; the weekend fade goes first.
struct WeekendBreakoutFade {
    struct Params {
        std::string instrument = "DOGE";
        Timeframe timeframe = Timeframe::Hour4;
        int hour = 8, span = 12, w_atr = 20, w_hold = 12;
        double tp_share = 0.3, w_stops = 1.75, w_leverage = 5.0;
        int window = 24, within = 4, b_atr = 14, b_hold = 8;
        double tp_atrs = 1.2, b_stops = 3.0, b_leverage = 5.0;
    } params;
    const Bars* bars = nullptr;
    std::vector<double> high, low, watr, batr;
    PriorMax high_of{1};
    PriorMin low_of{1};
    Atr watr_of{1}, batr_of{1};
    size_t done = 0;
    int seen_bar = -1, hold = 0;

    void prepare(const Markets& m) {
        high.clear(); low.clear(); watr.clear(); batr.clear(); done = 0; seen_bar = -1; hold = 0;
        high_of = PriorMax(params.window); low_of = PriorMin(params.window);
        watr_of = Atr(params.w_atr); batr_of = Atr(params.b_atr);
        update(m);
    }
    void update(const Markets& m) {
        bars = &m.at(params.instrument, params.timeframe);
        const Bars& b = *bars;
        for (; done < b.ts.size(); ++done) {
            size_t i = done;
            high.push_back(high_of.update(b.high[i]));
            low.push_back(low_of.update(b.low[i]));
            watr.push_back(watr_of.update(b.high[i], b.low[i], b.close[i]));
            batr.push_back(batr_of.update(b.high[i], b.low[i], b.close[i]));
        }
    }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        const auto& p = params;
        int t = last_closed(*bars, now);
        if (t < std::max(1, p.span) || t == seen_bar) return {};
        seen_bar = t;
        auto mine = std::find_if(positions.begin(), positions.end(),
                                 [&](const Position& x) { return x.instrument == p.instrument; });
        if (mine != positions.end()) {
            // hold is 0 after a restart; then the longer of the two is used.
            int h = hold ? hold : std::max(p.w_hold, p.b_hold);
            if (mine->entry_time != unknown_time && now - mine->entry_time >= h * seconds(p.timeframe))
                return {Order{.kind = Order::Kind::Close, .instrument = p.instrument}};
            return {};
        }
        const auto& c = bars->close;
        int64_t end = bars->ts[t] + seconds(p.timeframe);
        bool monday = (end / 86400 + 3) % 7 == 0 && (end / 3600) % 24 == p.hour;
        if (monday && defined(watr[t])) {
            double move = c[t] - c[t - p.span];
            if (move != 0) {
                hold = p.w_hold;
                return {Order{.kind = Order::Kind::Open, .instrument = p.instrument,
                              .side = move > 0 ? Side::Short : Side::Long, .stop_distance = p.w_stops * watr[t] / c[t],
                              .take_profit_distance = p.tp_share * std::abs(move) / c[t], .leverage = p.w_leverage}};
            }
        }
        if (!defined(batr[t])) return {};
        bool up = false, down = false;
        for (int j = t - 1; j >= std::max(0, t - p.within) && !up && !down; --j) {
            if (!defined(high[j]) || !defined(low[j])) break;
            up = c[j] > high[j] && c[t] < high[j] && c[t - 1] >= high[j];
            down = c[j] < low[j] && c[t] > low[j] && c[t - 1] <= low[j];
        }
        if (!up && !down) return {};
        double a = batr[t] / c[t];
        hold = p.b_hold;
        return {Order{.kind = Order::Kind::Open, .instrument = p.instrument, .side = up ? Side::Short : Side::Long,
                      .stop_distance = p.b_stops * a, .take_profit_distance = p.tp_atrs * a, .leverage = p.b_leverage}};
    }
};
static_assert(Strategy<WeekendBreakoutFade>);

namespace detail {
inline StrategyInfo weekend_breakout_fade_info() {
    using P = WeekendBreakoutFade::Params;
    return single<WeekendBreakoutFade>("weekend_breakout_fade", {
        field("instrument", &P::instrument), field("timeframe", &P::timeframe),
        field("hour", &P::hour, 0, 23), field("span", &P::span, 1, inf),
        field("w_atr", &P::w_atr, 1, inf), field("w_hold", &P::w_hold, 1, inf),
        field("tp_share", &P::tp_share, 0.0, inf), field("w_stops", &P::w_stops, 0.0, inf),
        field("w_leverage", &P::w_leverage, 0.0, 100.0), field("window", &P::window, 2, inf),
        field("within", &P::within, 1, inf), field("b_atr", &P::b_atr, 1, inf),
        field("b_hold", &P::b_hold, 1, inf), field("tp_atrs", &P::tp_atrs, 0.0, inf),
        field("b_stops", &P::b_stops, 0.0, inf), field("b_leverage", &P::b_leverage, 0.0, 100.0)},
        {Timeframe::Min15, Timeframe::Hour4});
}
}  // namespace detail

}
