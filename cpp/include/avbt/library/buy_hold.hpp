#pragma once
#include <cstdint>
#include <string>
#include <vector>

#include "avbt/registry.hpp"

namespace avbt {

// Buy and hold: one instrument. Opens once, at the first closed bar, and
// never closes; the backtest closes it at the end of data. After a stop it
// stays out. stop_loss is the fraction of collateral the stop loses, so the
// stop sits stop_loss / leverage from the fill.
struct BuyHold {
    struct Params {
        std::string instrument = "BTC";
        Timeframe timeframe = Timeframe::Min1;
        Side side = Side::Long;
        double stop_loss = max_stop_loss, leverage = 1.0;
    } params;
    const Bars* bars = nullptr;
    bool opened = false;

    void prepare(const Markets& m) {
        opened = false;
        update(m);
    }
    void update(const Markets& m) { bars = &m.at(params.instrument, params.timeframe); }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        // A position already there (after a restart) counts as opened.
        for (const Position& x : positions) opened = opened || x.instrument == params.instrument;
        if (opened || last_closed(*bars, now) < 0) return {};
        opened = true;
        const auto& p = params;
        // A hair inside, so float error never pushes the loss past max_stop_loss.
        double stop = p.stop_loss / p.leverage * (1 - 1e-9);
        return {Order{.kind = Order::Kind::Open, .instrument = p.instrument, .side = p.side,
                      .stop_distance = stop, .leverage = p.leverage}};
    }
};
static_assert(Strategy<BuyHold>);

namespace detail {
inline StrategyInfo buy_hold_info() {
    using P = BuyHold::Params;
    return single<BuyHold>("buy_hold", {
        field("instrument", &P::instrument), field("timeframe", &P::timeframe), side_field<P>(),
        field("stop_loss", &P::stop_loss, 0.0, max_stop_loss),
        field("leverage", &P::leverage, 0.0, 500.0)}, {Timeframe::Min1});
}
}  // namespace detail

}
