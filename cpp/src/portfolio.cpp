#include "avbt/portfolio.hpp"

#include <algorithm>
#include <cmath>

namespace avbt {

namespace {

// +1 for a long, -1 for a short: the sign of profit per unit of price rise.
double direction(Side side) { return side == Side::Long ? 1.0 : -1.0; }

}

double profit_share(const std::vector<Tier>& tiers, double gross, double collateral) {
    if (tiers.empty() || gross <= 0.0) return 0.0;
    double roi = gross / collateral;
    for (const Tier& t : tiers) {
        // 1e-9 keeps a ROI on a boundary, such as 5.0, in its tier.
        if (roi <= t.max_roi + 1e-9) return t.share * gross;
    }
    return tiers.back().share * gross;
}

bool Portfolio::open(const std::string& instrument, Side side, double entry_price,
                     double stop_price, double leverage,
                     double take_profit_price, Fees fees,
                     double trail, double take_profit_fraction, int64_t entry_time,
                     double spread, double min_position) {
    if (halted_ || leverage <= 0.0 || entry_price <= 0.0 || trail < 0.0) return false;
    if (!(take_profit_fraction > 0.0 && take_profit_fraction <= 1.0)) return false;
    for (const Position& p : positions_) {
        if (p.instrument == instrument) return false;
    }
    double dir = direction(side);
    // Distance from entry to stop, positive only when the stop is on the losing side.
    if (dir * (entry_price - stop_price) <= 0.0) return false;
    if (dir * (take_profit_price - entry_price) <= 0.0) return false;

    // The fill, and the loss per unit if the stop fills at its level. ADR 0020.
    double fill = entry_price * (1.0 + dir * spread);
    double distance = dir * (fill - stop_price * (1.0 - dir * spread));
    double risk = settings_.risk_per_trade * balance_;
    if (settings_.scale_risk_with_leverage) risk *= leverage;
    double size = risk / distance;
    if (size * fill < min_position) return false;
    double collateral = size * fill / leverage;
    if (risk > max_stop_loss * collateral) return false;
    if (collateral > report().free_cash) return false;

    // The price move that loses liquidation_loss of the collateral.
    double liquidation_move = liquidation_loss * collateral / size;
    positions_.push_back(Position{
        .instrument = instrument,
        .side = side,
        .entry_price = fill,
        .size = size,
        .collateral = collateral,
        .stop_price = stop_price,
        .take_profit_price = take_profit_price,
        .fees = fees,
        .leverage = leverage,
        .liquidation_price = fill - dir * liquidation_move,
        .mark_price = entry_price,
        .trail = trail,
        .take_profit_fraction = take_profit_fraction,
        .entry_time = entry_time,
        .spread = spread,
        .spread_cost = size * entry_price * spread,
    });
    balance_ -= fees.open * size * fill;
    return true;
}

std::optional<Closed> Portfolio::close(const std::string& instrument, double price,
                                       double fraction, Cause cause, double spread) {
    auto it = std::find_if(positions_.begin(), positions_.end(),
                           [&](const Position& p) { return p.instrument == instrument; });
    if (it == positions_.end() || fraction <= 0.0) return std::nullopt;
    fraction = std::min(fraction, 1.0);
    // A close whose fill, spread included, is at or past the liquidation
    // price liquidates the whole position there with no spread: the loss
    // never passes liquidation_loss. A gap past it liquidates before any
    // order can close. ADR 0020.
    double dir = direction(it->side);
    if (dir * (price * (1.0 - dir * spread) - it->liquidation_price) <= 0.0) {
        fraction = 1.0;
        price = it->liquidation_price;
        spread = 0.0;
        cause = Cause::Liquidation;
    }

    double size = it->size * fraction;
    // The oracle price moved against the position by the spread. ADR 0020.
    double spread_cost = it->spread_cost * fraction + size * price * spread;
    price *= 1.0 - direction(it->side) * spread;
    double close_fee = it->fees.close * size * price;
    double open_fee = it->fees.open * size * it->entry_price;
    double holding = it->holding * fraction;
    double gross = direction(it->side) * (price - it->entry_price) * size;
    // A liquidation's fee is what the loss leaves of the collateral: the
    // trader gets nothing back. ADR 0023.
    if (cause == Cause::Liquidation) {
        close_fee = std::max(0.0, it->collateral + gross - holding);
    }
    // Priced on this part's own ROI, before holding costs; 0 with no tiers. ADR 0019.
    double share = profit_share(it->fees.profit_share, gross, it->collateral * fraction);
    // The open fee left the balance at the open; the close fee and holding costs leave now.
    balance_ += gross - close_fee - holding;
    // Apart, so the size-fee sums above keep their exact form.
    balance_ -= share;
    it->holding -= holding;
    it->spread_cost -= it->spread_cost * fraction;
    Closed closed{.instrument = it->instrument, .side = it->side,
                  .entry_price = it->entry_price, .exit_price = price, .size = size,
                  .leverage = it->leverage, .fees = open_fee + close_fee + share, .holding = holding,
                  .spread_cost = spread_cost,
                  .result = gross - close_fee - open_fee - holding - share, .cause = cause};
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
        p.spread = q.spread;
        bool is_long = p.side == Side::Long;

        // The worst price the bar reached against the position.
        double worst = is_long ? q.low : q.high;
        bool stop_hit = is_long ? worst <= p.stop_price : worst >= p.stop_price;
        // The best price the bar reached for the position. NaN compares false.
        double best = is_long ? q.high : q.low;
        bool take_hit = is_long ? best >= p.take_profit_price : best <= p.take_profit_price;
        // A bar that opens past the take profit took it first, even if it later
        // reaches the stop (ADR 0003).
        bool past = is_long ? q.open > p.take_profit_price : q.open < p.take_profit_price;
        if (take_hit && (!stop_hit || past)) {
            double fraction = p.take_profit_fraction;
            out.push_back(*close(p.instrument, past ? q.open : p.take_profit_price, fraction,
                                 fraction < 1.0 ? Cause::PartialTakeProfit : Cause::TakeProfit, q.spread));
            // A full close, or a liquidation, leaves no position.
            if (fraction >= 1.0 || out.back().cause == Cause::Liquidation) continue;
            // A partial close keeps p in place; the rest runs on its stop.
            p.take_profit_price = std::nan("");
        }
        if (!stop_hit) {
            p.mark_price = q.close;
            if (p.trail > 0.0) {
                double moved = is_long ? std::max(p.stop_price, q.high - p.trail)
                                       : std::min(p.stop_price, q.low + p.trail);
                if (moved != p.stop_price) p.trailed = true;
                p.stop_price = moved;
            }
            continue;
        }
        // A bar that opens past the stop fills at the open.
        bool gapped = is_long ? q.open < p.stop_price : q.open > p.stop_price;
        double fill = gapped ? q.open : p.stop_price;
        out.push_back(*close(p.instrument, fill, 1.0, p.trailed ? Cause::TrailingStop : Cause::Stop, q.spread));
    }

    double floor = settings_.starting_balance * (1.0 - settings_.hard_stop);
    if (!halted_ && report().equity <= floor) {
        while (!positions_.empty()) {
            const Position& p = positions_.front();
            out.push_back(*close(p.instrument, p.mark_price, 1.0, Cause::HardStop, p.spread));
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
    // No tiers: the size-fee form, unchanged, so equity stays bit for bit.
    if (p.fees.profit_share.empty()) {
        return direction(p.side) * (p.mark_price - p.entry_price) * p.size - p.holding;
    }
    double gross = direction(p.side) * (p.mark_price - p.entry_price) * p.size;
    return gross - profit_share(p.fees.profit_share, gross, p.collateral) - p.holding;
}

}
