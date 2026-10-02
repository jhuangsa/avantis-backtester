#pragma once
#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

#include "avbt/registry.hpp"

namespace avbt {

// Dip basket: every market passed in, one account. A market is up when its
// closed trend bar closes above its trend_period average (down: below).
// Buy a market up whose close fell more than `drop` of its own ATRs over
// `lookback` signal bars, but only while at least `breadth` of the markets
// are up (the whole basket in an uptrend); with shorts, sell a market down
// that rose that much while `breadth` are down. Take profit tp_atrs ATRs,
// stop atr_stops ATRs, out after `hold` signal bars or (with revert) once
// the lookback move turns back. At most max_open positions; a market-wide
// crash hits every open dip at once.
struct DipBasket {
    struct Params {
        Timeframe timeframe = Timeframe::Hour1, trend_timeframe = Timeframe::Hour4;
        int trend_period = 50, lookback = 4, atr_period = 14, hold = 12, max_open = 5;
        double drop = 3.0, breadth = 0.6, tp_atrs = 1.5, atr_stops = 4.0, leverage = 5.0;
        bool shorts = true, revert = true;
    } params;
    struct Lines {
        std::string instrument;
        const Bars *bars = nullptr, *trend_bars = nullptr;
        std::vector<double> ret, atr, trend_avg;
        PctChange ret_of{1};
        Atr atr_of{1};
        Sma trend_of{1};
        size_t done = 0, trend_done = 0;
    };
    std::vector<Lines> lines;
    int64_t seen_ts = -1;

    bool trades(const std::string& instrument) const {
        return std::any_of(lines.begin(), lines.end(), [&](const Lines& l) { return l.instrument == instrument; });
    }
    void prepare(const Markets& m) {
        lines.clear();
        seen_ts = -1;
        update(m);
    }
    void update(const Markets& m) {
        for (const Market& market : m.all()) {
            auto it = std::find_if(lines.begin(), lines.end(),
                                   [&](const Lines& l) { return l.instrument == market.instrument; });
            if (it == lines.end()) {
                lines.push_back(Lines{.instrument = market.instrument, .ret_of = PctChange(params.lookback),
                                      .atr_of = Atr(params.atr_period),
                                      .trend_of = Sma(params.trend_period)});
                it = lines.end() - 1;
            }
            Lines& l = *it;
            l.bars = &m.at(l.instrument, params.timeframe);
            l.trend_bars = &m.at(l.instrument, params.trend_timeframe);
            for (; l.done < l.bars->ts.size(); ++l.done) {
                size_t i = l.done;
                l.ret.push_back(l.ret_of.update(l.bars->close[i]));
                l.atr.push_back(l.atr_of.update(l.bars->high[i], l.bars->low[i], l.bars->close[i]));
            }
            for (; l.trend_done < l.trend_bars->ts.size(); ++l.trend_done)
                l.trend_avg.push_back(l.trend_of.update(l.trend_bars->close[l.trend_done]));
        }
    }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        if (lines.empty()) return {};
        int c = last_closed(*lines[0].bars, now);
        if (c < 0 || lines[0].bars->ts[c] == seen_ts) return {};
        seen_ts = lines[0].bars->ts[c];
        const auto& p = params;
        auto held = [&](const std::string& ins) {
            return std::find_if(positions.begin(), positions.end(), [&](const Position& x) { return x.instrument == ins; });
        };
        std::vector<Order> orders;
        int open = 0;
        auto line = [&](const std::string& ins) {
            return std::find_if(lines.begin(), lines.end(), [&](const Lines& l) { return l.instrument == ins; });
        };
        for (const Position& x : positions) {
            if (!trades(x.instrument)) continue;
            ++open;
            const Lines& l = *line(x.instrument);
            int t = last_closed(*l.bars, now);
            bool back = p.revert && t >= 0 && defined(l.ret[t]) &&
                        (x.side == Side::Long ? l.ret[t] > 0 : l.ret[t] < 0);
            bool late = x.entry_time != unknown_time && now - x.entry_time >= p.hold * seconds(p.timeframe);
            if (late || back)
                orders.push_back(Order{.kind = Order::Kind::Close, .instrument = x.instrument}), --open;
        }
        // Breadth: share of markets with a defined trend that are up / down.
        int ups = 0, downs = 0, n = 0;
        for (const Lines& l : lines) {
            int u = last_closed(*l.trend_bars, now);
            if (u < 0 || !defined(l.trend_avg[u])) continue;
            ++n, ups += l.trend_bars->close[u] > l.trend_avg[u], downs += l.trend_bars->close[u] < l.trend_avg[u];
        }
        if (n == 0) return orders;
        bool bull = ups >= p.breadth * n, bear = p.shorts && downs >= p.breadth * n;
        for (const Lines& l : lines) {
            if (open >= p.max_open) break;
            int t = last_closed(*l.bars, now), u = last_closed(*l.trend_bars, now);
            if (t < 0 || u < 0 || held(l.instrument) != positions.end()) continue;
            if (!defined(l.ret[t]) || !defined(l.atr[t]) || !defined(l.trend_avg[u])) continue;
            double c = l.bars->close[t], a = l.atr[t] / c;
            // The move in ATRs: ret is a share of the price lookback bars ago.
            double r = l.ret[t] / (1 + l.ret[t]) / a;
            bool buy = bull && r < -p.drop && l.trend_bars->close[u] > l.trend_avg[u];
            bool sell = bear && r > p.drop && l.trend_bars->close[u] < l.trend_avg[u];
            if (!buy && !sell) continue;
            orders.push_back(Order{.kind = Order::Kind::Open, .instrument = l.instrument,
                                   .side = buy ? Side::Long : Side::Short, .stop_distance = p.atr_stops * a,
                                   .take_profit_distance = p.tp_atrs * a, .leverage = p.leverage});
            ++open;
        }
        return orders;
    }
};
static_assert(Strategy<DipBasket>);

namespace detail {
inline StrategyInfo dip_basket_info() {
    using P = DipBasket::Params;
    return single<DipBasket>("dip_basket", {
        field("timeframe", &P::timeframe), field("trend_timeframe", &P::trend_timeframe),
        field("trend_period", &P::trend_period, 1, inf), field("lookback", &P::lookback, 1, inf),
        field("atr_period", &P::atr_period, 1, inf), field("hold", &P::hold, 1, inf),
        field("max_open", &P::max_open, 1, inf), field("drop", &P::drop, 0.0, inf),
        field("breadth", &P::breadth, 0.0, 1.0), field("tp_atrs", &P::tp_atrs, 0.0, inf),
        field("atr_stops", &P::atr_stops, 0.0, inf), field("leverage", &P::leverage, 0.0, 100.0),
        field("shorts", &P::shorts), field("revert", &P::revert)},
        {Timeframe::Min15, Timeframe::Hour1});
}
}  // namespace detail

}
