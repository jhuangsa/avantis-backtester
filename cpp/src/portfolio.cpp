#include "avbt/portfolio.hpp"

#include <algorithm>
#include <cmath>

namespace avbt {

namespace {

// +1 for a long, -1 for a short: the sign of profit per unit of price rise.
double direction(Side side) { return side == Side::Long ? 1.0 : -1.0; }

}

bool Portfolio::open(const std::string& instrument, Side side, double entry_price,
                     double stop_price, double leverage) {
    if (halted_ || leverage <= 0.0 || entry_price <= 0.0) return false;
    for (const Position& p : positions_) {
        if (p.instrument == instrument) return false;
    }
    // Distance from entry to stop, positive only when the stop is on the losing side.
    double distance = direction(side) * (entry_price - stop_price);
    if (distance <= 0.0) return false;

    double risk = settings_.risk_per_trade * balance_;
    double size = risk / distance;
    double collateral = size * entry_price / leverage;
    if (risk > max_stop_loss * collateral) return false;
    if (collateral > report().free_cash) return false;

    // The price move that loses liquidation_loss of the collateral.
    double liquidation_move = liquidation_loss * collateral / size;
    positions_.push_back(Position{
        .instrument = instrument,
        .side = side,
        .entry_price = entry_price,
        .size = size,
        .collateral = collateral,
        .stop_price = stop_price,
        .liquidation_price = entry_price - direction(side) * liquidation_move,
        .mark_price = entry_price,
    });
    return true;
}

double Portfolio::close(const std::string& instrument, double price, double fraction) {
    auto it = std::find_if(positions_.begin(), positions_.end(),
                           [&](const Position& p) { return p.instrument == instrument; });
    if (it == positions_.end() || fraction <= 0.0) return 0.0;
    fraction = std::min(fraction, 1.0);

    double result = direction(it->side) * (price - it->entry_price) * it->size * fraction;
    balance_ += result;
    if (fraction >= 1.0) {
        positions_.erase(it);
    } else {
        // Size and collateral shrink together, so the liquidation price holds.
        it->size *= 1.0 - fraction;
        it->collateral *= 1.0 - fraction;
    }
    return result;
}

void Portfolio::check(const std::vector<Quote>& quotes) {
    for (const Quote& q : quotes) {
        auto it = std::find_if(positions_.begin(), positions_.end(),
                               [&](const Position& p) { return p.instrument == q.instrument; });
        if (it == positions_.end()) continue;
        Position& p = *it;
        bool is_long = p.side == Side::Long;

        // The worst price the bar reached against the position.
        double worst = is_long ? q.low : q.high;
        bool stop_hit = is_long ? worst <= p.stop_price : worst >= p.stop_price;
        if (!stop_hit) {
            p.mark_price = q.close;
            continue;
        }
        // A bar that opens past the stop fills at the open. A gap past the
        // liquidation price too fills there: the loss never passes liquidation_loss.
        bool gapped = is_long ? q.open < p.stop_price : q.open > p.stop_price;
        double fill = gapped ? q.open : p.stop_price;
        bool liquidated = is_long ? fill <= p.liquidation_price : fill >= p.liquidation_price;
        close(p.instrument, liquidated ? p.liquidation_price : fill);
    }

    double floor = settings_.starting_balance * (1.0 - settings_.hard_stop);
    if (!halted_ && report().equity <= floor) {
        while (!positions_.empty()) {
            close(positions_.front().instrument, positions_.front().mark_price);
        }
        halted_ = true;
    }
}

Report Portfolio::report() const {
    Report r{.balance = balance_, .equity = balance_, .free_cash = balance_,
             .open_positions = static_cast<int>(positions_.size()), .halted = halted_};
    for (const Position& p : positions_) {
        r.equity += unrealized(p);
        r.free_cash -= p.collateral;
    }
    return r;
}

double Portfolio::unrealized(const Position& p) const {
    return direction(p.side) * (p.mark_price - p.entry_price) * p.size;
}

}
