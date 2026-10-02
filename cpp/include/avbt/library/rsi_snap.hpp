#pragma once
#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

#include "avbt/registry.hpp"

namespace avbt {

// Wilder RSI over n bars, one close at a time; NaN until n changes are in.
class Rsi {
public:
    explicit Rsi(int n) : n_(n) {}
    double update(double x) {
        if (std::isnan(prev_)) return prev_ = x, std::nan("");
        double d = x - prev_, up = std::max(d, 0.0), down = std::max(-d, 0.0);
        prev_ = x;
        if (count_ < n_) {
            up_ += up / n_, down_ += down / n_;
            if (++count_ < n_) return std::nan("");
        } else {
            up_ = (up_ * (n_ - 1) + up) / n_, down_ = (down_ * (n_ - 1) + down) / n_;
        }
        return up_ + down_ <= 0 ? 50.0 : 100.0 * up_ / (up_ + down_);
    }
private:
    int n_, count_ = 0;
    double prev_ = std::nan(""), up_ = 0, down_ = 0;
};

// RSI snap: one instrument (a short-period RSI pullback). When the closed
// signal bar's close is above its trend_period average and the RSI is
// below `low`, buy; with shorts, below the average and the RSI above
// 100 - low, sell. Out when the close crosses back over its exit_period
// average or after `hold` bars. Take profit tp_atrs ATRs, a wide stop of
// atr_stops ATRs.
struct RsiSnap {
    struct Params {
        std::string instrument = "ETH";
        Timeframe timeframe = Timeframe::Min15;
        int rsi_period = 2, trend_period = 200, exit_period = 5, atr_period = 14, hold = 24;
        double low = 10.0, tp_atrs = 2.0, atr_stops = 4.0, leverage = 8.0;
        bool shorts = true;
    } params;
    const Bars* bars = nullptr;
    std::vector<double> rsi, trend, exit_avg, atr;
    Rsi rsi_of{1};
    Sma trend_of{1}, exit_of{1};
    Atr atr_of{1};
    size_t done = 0;
    int seen_bar = -1;

    void prepare(const Markets& m) {
        rsi.clear(); trend.clear(); exit_avg.clear(); atr.clear(); done = 0; seen_bar = -1;
        rsi_of = Rsi(params.rsi_period); trend_of = Sma(params.trend_period);
        exit_of = Sma(params.exit_period); atr_of = Atr(params.atr_period);
        update(m);
    }
    void update(const Markets& m) {
        bars = &m.at(params.instrument, params.timeframe);
        for (; done < bars->ts.size(); ++done) {
            double c = bars->close[done];
            rsi.push_back(rsi_of.update(c));
            trend.push_back(trend_of.update(c));
            exit_avg.push_back(exit_of.update(c));
            atr.push_back(atr_of.update(bars->high[done], bars->low[done], c));
        }
    }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        int t = last_closed(*bars, now);
        if (t < 0 || t == seen_bar) return {};
        seen_bar = t;
        const auto& p = params;
        double c = bars->close[t];
        auto mine = std::find_if(positions.begin(), positions.end(),
                                 [&](const Position& x) { return x.instrument == p.instrument; });
        if (mine != positions.end()) {
            bool late = mine->entry_time != unknown_time &&
                        now - mine->entry_time >= p.hold * seconds(p.timeframe);
            bool back = defined(exit_avg[t]) && (mine->side == Side::Long ? c > exit_avg[t] : c < exit_avg[t]);
            if (late || back) return {Order{.kind = Order::Kind::Close, .instrument = p.instrument}};
            return {};
        }
        if (!defined(rsi[t]) || !defined(trend[t]) || !defined(atr[t])) return {};
        bool buy = c > trend[t] && rsi[t] < p.low;
        bool sell = p.shorts && c < trend[t] && rsi[t] > 100 - p.low;
        if (!buy && !sell) return {};
        return {Order{.kind = Order::Kind::Open, .instrument = p.instrument,
                      .side = buy ? Side::Long : Side::Short, .stop_distance = p.atr_stops * atr[t] / c,
                      .take_profit_distance = p.tp_atrs * atr[t] / c, .leverage = p.leverage}};
    }
};
static_assert(Strategy<RsiSnap>);

namespace detail {
inline StrategyInfo rsi_snap_info() {
    using P = RsiSnap::Params;
    return single<RsiSnap>("rsi_snap", {
        field("instrument", &P::instrument), field("timeframe", &P::timeframe),
        field("rsi_period", &P::rsi_period, 1, inf), field("trend_period", &P::trend_period, 1, inf),
        field("exit_period", &P::exit_period, 1, inf), field("atr_period", &P::atr_period, 1, inf),
        field("hold", &P::hold, 1, inf), field("low", &P::low, 0.0, 50.0),
        field("tp_atrs", &P::tp_atrs, 0.0, inf), field("atr_stops", &P::atr_stops, 0.0, inf),
        field("leverage", &P::leverage, 0.0, 100.0), field("shorts", &P::shorts)},
        {Timeframe::Min15, Timeframe::Hour1});
}
}  // namespace detail

}
