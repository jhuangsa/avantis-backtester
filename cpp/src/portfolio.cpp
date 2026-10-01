#include "avbt/portfolio.hpp"

#include <algorithm>
#include <cmath>

namespace avbt {

namespace {

// +1 for a long, -1 for a short: the sign of profit per unit of price rise.
double direction(Side side) { return side == Side::Long ? 1.0 : -1.0; }

}

bool Portfolio::open(const std::string& instrument, Side side, double entry_price,
                     double stop_price, double leverage,
                     double take_profit_price, Fees fees,
                     double trail, double take_profit_fraction) {
    if (halted_ || leverage <= 0.0 || entry_price <= 0.0 || trail < 0.0) return false;
    if (!(take_profit_fraction > 0.0 && take_profit_fraction <= 1.0)) return false;
    for (const Position& p : positions_) {
        if (p.instrument == instrument) return false;
    }
    // Distance from entry to stop, positive only when the stop is on the losing side.
    double distance = direction(side) * (entry_price - stop_price);
    if (distance <= 0.0) return false;
    if (direction(side) * (take_profit_price - entry_price) <= 0.0) return false;

    double risk = settings_.risk_per_trade * balance_;
    if (settings_.scale_risk_with_leverage) risk *= leverage;
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
        .take_profit_price = take_profit_price,
        .fees = fees,
        .leverage = leverage,
        .liquidation_price = entry_price - direction(side) * liquidation_move,
        .mark_price = entry_price,
        .trail = trail,
        .take_profit_fraction = take_profit_fraction,
    });
    balance_ -= fees.open * size * entry_price;
    return true;
}

std::optional<Closed> Portfolio::close(const std::string& instrument, double price,
                                       double fraction, Cause cause) {
    auto it = std::find_if(positions_.begin(), positions_.end(),
                           [&](const Position& p) { return p.instrument == instrument; });
    if (it == positions_.end() || fraction <= 0.0) return std::nullopt;
    fraction = std::min(fraction, 1.0);

    double size = it->size * fraction;
    double close_fee = it->fees.close * size * price;
    double open_fee = it->fees.open * size * it->entry_price;
    double holding = it->holding * fraction;
    double gross = direction(it->side) * (price - it->entry_price) * size;
    // The open fee left the balance at the open; the close fee and holding costs leave now.
    balance_ += gross - close_fee - holding;
    it->holding -= holding;
    Closed closed{.instrument = it->instrument, .side = it->side,
                  .entry_price = it->entry_price, .exit_price = price, .size = size,
                  .leverage = it->leverage, .fees = open_fee + close_fee, .holding = holding,
                  .result = gross - close_fee - open_fee - holding, .cause = cause};
    if (fraction >= 1.0) {
        positions_.erase(it);
    } else {
        // Size and collateral shrink together, so the liquidation price holds.
        it->size *= 1.0 - fraction;
        it->collateral *= 1.0 - fraction;
    }
    return closed;
}

void Portfolio::hold(const std::string& instrument, double rate, double price) {
    if (std::isnan(rate)) return;
    for (Position& p : positions_) {
        if (p.instrument == instrument) p.holding += rate * p.size * price;
    }
}

std::vector<Closed> Portfolio::check(const std::vector<Quote>& quotes) {
    std::vector<Closed> out;
    for (const Quote& q : quotes) {
        auto it = std::find_if(positions_.begin(), positions_.end(),
                               [&](const Position& p) { return p.instrument == q.instrument; });
        if (it == positions_.end()) continue;
        Position& p = *it;
        bool is_long = p.side == Side::Long;

        // The worst price the bar reached against the position.
        double worst = is_long ? q.low : q.high;
        bool stop_hit = is_long ? worst <= p.stop_price : worst >= p.stop_price;
        // The best price the bar reached for the position. NaN compares false.
        double best = is_long ? q.high : q.low;
        bool take_hit = is_long ? best >= p.take_profit_price : best <= p.take_profit_price;
        if (!stop_hit && take_hit) {
            bool past = is_long ? q.open > p.take_profit_price : q.open < p.take_profit_price;
            double fraction = p.take_profit_fraction;
            out.push_back(*close(p.instrument, past ? q.open : p.take_profit_price,
                                 fraction, fraction < 1.0 ? Cause::PartialTakeProfit : Cause::TakeProfit));
            if (fraction >= 1.0) continue;
            // A partial close keeps p in place; the rest runs on its stop.
            p.take_profit_price = std::nan("");
        }
        if (!stop_hit) {
            p.mark_price = q.close;
            if (p.trail > 0.0) {
                p.stop_price = is_long ? std::max(p.stop_price, q.high - p.trail)
                                       : std::min(p.stop_price, q.low + p.trail);
            }
            continue;
        }
        // A bar that opens past the stop fills at the open. A gap past the
        // liquidation price too fills there: the loss never passes liquidation_loss.
        bool gapped = is_long ? q.open < p.stop_price : q.open > p.stop_price;
        double fill = gapped ? q.open : p.stop_price;
        bool liquidated = is_long ? fill <= p.liquidation_price : fill >= p.liquidation_price;
        out.push_back(*close(p.instrument, liquidated ? p.liquidation_price : fill, 1.0,
                             liquidated ? Cause::Liquidation
                             : p.trail > 0.0 ? Cause::TrailingStop : Cause::Stop));
    }

    double floor = settings_.starting_balance * (1.0 - settings_.hard_stop);
    if (!halted_ && report().equity <= floor) {
        while (!positions_.empty()) {
            out.push_back(*close(positions_.front().instrument,
                                 positions_.front().mark_price, 1.0, Cause::HardStop));
        }
        halted_ = true;
    }
    return out;
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
    return direction(p.side) * (p.mark_price - p.entry_price) * p.size - p.holding;
}

}
