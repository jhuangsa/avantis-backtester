#pragma once
#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

#include "avbt/library/rsi_snap.hpp"
#include "avbt/registry.hpp"

namespace avbt {

// Mimic: every market in Markets, one set of switchable filters, built to
// be fitted to a wallet's PnL. Each filter that is on must agree before an
// open; with all off it opens on every closed signal bar. s is +1 for a
// long, -1 for a short; a filter's dir is +1 to follow, -1 to fade.
//   rsi:      long when RSI < rsi_level, short when RSI > 100 - rsi_level.
//   trend:    s * trend_dir * (close - SMA(trend_period)) > 0.
//   move:     s * move_dir * pct_change(close, move_lag) >= move_size.
//   breakout: close above the prior break_period high is a long with
//             break_dir +1, a short with -1; below the prior low, the reverse.
//   hours:    the signal bar opens at a UTC hour in [hour_from, hour_to).
// side: +1 longs only, -1 shorts only, 0 both; a bar where both pass is
// skipped. Out at stop_atrs ATRs, take profit tp_atrs ATRs, or after hold bars.
struct Mimic {
    struct Params {
        Timeframe timeframe = Timeframe::Hour1;
        int side = 0;
        bool rsi_on = false, trend_on = false, move_on = false, break_on = false, hours_on = false;
        int rsi_period = 14, trend_period = 50, trend_dir = 1, move_lag = 4, move_dir = -1;
        int break_period = 24, break_dir = 1, hour_from = 0, hour_to = 24, atr_period = 14, hold = 24;
        double rsi_level = 30.0, move_size = 0.03, stop_atrs = 3.0, tp_atrs = 3.0, leverage = 2.0;
    } params;

    struct Lines {
        std::string instrument;
        const Bars* bars = nullptr;
        std::vector<double> rsi, trend, move, high, low, atr;
        Rsi rsi_of{1};
        Sma trend_of{1};
        PctChange move_of{1};
        PriorMax high_of{1};
        PriorMin low_of{1};
        Atr atr_of{1};
        size_t done = 0;
        int seen = -1;
    };
    std::vector<Lines> lines;

    void prepare(const Markets& m) {
        const auto& p = params;
        lines.clear();
        for (const Market& x : m.all()) {
            Lines l{.instrument = x.instrument, .rsi_of = Rsi(p.rsi_period), .trend_of = Sma(p.trend_period),
                    .move_of = PctChange(p.move_lag), .high_of = PriorMax(p.break_period),
                    .low_of = PriorMin(p.break_period), .atr_of = Atr(p.atr_period)};
            lines.push_back(std::move(l));
        }
        update(m);
    }
    void update(const Markets& m) {
        for (Lines& l : lines) {
            l.bars = &m.at(l.instrument, params.timeframe);
            const Bars& b = *l.bars;
            for (; l.done < b.ts.size(); ++l.done) {
                size_t i = l.done;
                double c = b.close[i];
                l.rsi.push_back(l.rsi_of.update(c));
                l.trend.push_back(l.trend_of.update(c));
                l.move.push_back(l.move_of.update(c));
                l.high.push_back(l.high_of.update(b.high[i]));
                l.low.push_back(l.low_of.update(b.low[i]));
                l.atr.push_back(l.atr_of.update(b.high[i], b.low[i], c));
            }
        }
    }

    // Whether every filter that is on agrees with side s at bar t.
    bool passes(const Lines& l, int t, int s) const {
        const auto& p = params;
        double c = l.bars->close[t];
        if (p.rsi_on && !(s > 0 ? l.rsi[t] < p.rsi_level : l.rsi[t] > 100 - p.rsi_level)) return false;
        if (p.trend_on && !(s * p.trend_dir * (c - l.trend[t]) > 0)) return false;
        if (p.move_on && !(s * p.move_dir * l.move[t] >= p.move_size)) return false;
        if (p.break_on) {
            int up = c > l.high[t] ? 1 : c < l.low[t] ? -1 : 0;
            if (up * p.break_dir != s) return false;
        }
        if (p.hours_on) {
            int h = static_cast<int>((l.bars->ts[t] / 3600) % 24);
            if (h < p.hour_from || h >= p.hour_to) return false;
        }
        return true;
    }

    // No open while a line that is on, or the ATR, is undefined.
    bool ready(const Lines& l, int t) const {
        const auto& p = params;
        return defined(l.atr[t]) && l.atr[t] > 0 && (!p.rsi_on || defined(l.rsi[t])) &&
               (!p.trend_on || defined(l.trend[t])) && (!p.move_on || defined(l.move[t])) &&
               (!p.break_on || (defined(l.high[t]) && defined(l.low[t])));
    }

    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        const auto& p = params;
        std::vector<Order> out;
        for (Lines& l : lines) {
            int t = last_closed(*l.bars, now);
            if (t < 0 || t == l.seen) continue;
            l.seen = t;
            auto mine = std::find_if(positions.begin(), positions.end(),
                                     [&](const Position& x) { return x.instrument == l.instrument; });
            if (mine != positions.end()) {
                if (mine->entry_time != unknown_time && now - mine->entry_time >= p.hold * seconds(p.timeframe)) {
                    out.push_back(Order{.kind = Order::Kind::Close, .instrument = l.instrument});
                }
                continue;
            }
            if (!ready(l, t)) continue;
            bool buy = p.side >= 0 && passes(l, t, 1);
            bool sell = p.side <= 0 && passes(l, t, -1);
            if (buy == sell) continue;
            double a = l.atr[t] / l.bars->close[t];
            out.push_back(Order{.kind = Order::Kind::Open, .instrument = l.instrument,
                                .side = buy ? Side::Long : Side::Short, .stop_distance = p.stop_atrs * a,
                                .take_profit_distance = p.tp_atrs * a, .leverage = p.leverage});
        }
        return out;
    }
};
static_assert(Strategy<Mimic>);

namespace detail {
inline StrategyInfo mimic_info() {
    using P = Mimic::Params;
    return single<Mimic>("mimic", {
        field("timeframe", &P::timeframe), field("side", &P::side, -1, 1),
        field("rsi_on", &P::rsi_on), field("trend_on", &P::trend_on), field("move_on", &P::move_on),
        field("break_on", &P::break_on), field("hours_on", &P::hours_on),
        field("rsi_period", &P::rsi_period, 1, inf), field("rsi_level", &P::rsi_level, 0.0, 50.0),
        field("trend_period", &P::trend_period, 1, inf), field("trend_dir", &P::trend_dir, -1, 1),
        field("move_lag", &P::move_lag, 1, inf), field("move_size", &P::move_size, 0.0, inf),
        field("move_dir", &P::move_dir, -1, 1), field("break_period", &P::break_period, 1, inf),
        field("break_dir", &P::break_dir, -1, 1), field("hour_from", &P::hour_from, 0, 23),
        field("hour_to", &P::hour_to, 1, 24), field("atr_period", &P::atr_period, 1, inf),
        field("hold", &P::hold, 1, inf), field("stop_atrs", &P::stop_atrs, 0.0, inf),
        field("tp_atrs", &P::tp_atrs, 0.0, inf), field("leverage", &P::leverage, 0.0, 100.0)},
        {Timeframe::Min15, Timeframe::Hour1, Timeframe::Hour4});
}
}  // namespace detail

}
