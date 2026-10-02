#pragma once
#include <algorithm>
#include <string>
#include <vector>

#include "avbt/registry.hpp"

namespace avbt {

// Dip buyer: one instrument. In an uptrend (the closed trend bar's close
// above its trend_period average), buy when a closed signal bar closes more
// than k ATRs below its avg_period average. With shorts, the mirror: in a
// downtrend, sell a close k ATRs above the average. Take profit tp_atrs
// ATRs, a wide stop of atr_stops ATRs, out after `hold` signal bars (with
// revert, also out once the close is back at the average).
struct DipBuyer {
    struct Params {
        std::string instrument = "SOL";
        Timeframe timeframe = Timeframe::Hour1, trend_timeframe = Timeframe::Hour4;
        int trend_period = 50, avg_period = 20, atr_period = 14, hold = 12;
        double k = 2.0, tp_atrs = 1.0, atr_stops = 4.0, leverage = 3.0;
        bool shorts = true, revert = false;
    } params;
    const Bars *bars = nullptr, *trend_bars = nullptr;
    std::vector<double> avg, atr, trend_avg;
    Sma avg_of{1}, trend_of{1};
    Atr atr_of{1};
    size_t done = 0, trend_done = 0;
    int seen_bar = -1;

    void prepare(const Markets& m) {
        avg.clear(); atr.clear(); trend_avg.clear(); done = trend_done = 0; seen_bar = -1;
        avg_of = Sma(params.avg_period); trend_of = Sma(params.trend_period); atr_of = Atr(params.atr_period);
        update(m);
    }
    void update(const Markets& m) {
        bars = &m.at(params.instrument, params.timeframe);
        trend_bars = &m.at(params.instrument, params.trend_timeframe);
        for (; done < bars->ts.size(); ++done) {
            avg.push_back(avg_of.update(bars->close[done]));
            atr.push_back(atr_of.update(bars->high[done], bars->low[done], bars->close[done]));
        }
        for (; trend_done < trend_bars->ts.size(); ++trend_done)
            trend_avg.push_back(trend_of.update(trend_bars->close[trend_done]));
    }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        int t = last_closed(*bars, now);
        if (t < 0 || t == seen_bar) return {};
        seen_bar = t;
        const auto& p = params;
        auto mine = std::find_if(positions.begin(), positions.end(),
                                 [&](const Position& x) { return x.instrument == p.instrument; });
        if (mine != positions.end()) {
            bool late = mine->entry_time != unknown_time &&
                        now - mine->entry_time >= p.hold * seconds(p.timeframe);
            bool back = p.revert && defined(avg[t]) &&
                        (mine->side == Side::Long ? bars->close[t] >= avg[t] : bars->close[t] <= avg[t]);
            if (late || back) return {Order{.kind = Order::Kind::Close, .instrument = p.instrument}};
            return {};
        }
        int u = last_closed(*trend_bars, now);
        if (u < 0 || !defined(trend_avg[u]) || !defined(avg[t]) || !defined(atr[t])) return {};
        bool up = trend_bars->close[u] > trend_avg[u], down = trend_bars->close[u] < trend_avg[u];
        double c = bars->close[t], gap = (c - avg[t]) / atr[t];
        bool buy = up && gap < -p.k, sell = p.shorts && down && gap > p.k;
        if (!buy && !sell) return {};
        return {Order{.kind = Order::Kind::Open, .instrument = p.instrument,
                      .side = buy ? Side::Long : Side::Short, .stop_distance = p.atr_stops * atr[t] / c,
                      .take_profit_distance = p.tp_atrs * atr[t] / c, .leverage = p.leverage}};
    }
};
static_assert(Strategy<DipBuyer>);

namespace detail {
inline StrategyInfo dip_buyer_info() {
    using P = DipBuyer::Params;
    return single<DipBuyer>("dip_buyer", {
        field("instrument", &P::instrument), field("timeframe", &P::timeframe),
        field("trend_timeframe", &P::trend_timeframe),
        field("trend_period", &P::trend_period, 1, inf), field("avg_period", &P::avg_period, 1, inf),
        field("atr_period", &P::atr_period, 1, inf), field("hold", &P::hold, 1, inf),
        field("k", &P::k, 0.0, inf), field("tp_atrs", &P::tp_atrs, 0.0, inf),
        field("atr_stops", &P::atr_stops, 0.0, inf), field("leverage", &P::leverage, 0.0, 100.0),
        field("shorts", &P::shorts), field("revert", &P::revert)}, {Timeframe::Min15, Timeframe::Hour1});
}
}  // namespace detail

}
