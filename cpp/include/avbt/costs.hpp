#pragma once
#include <cmath>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

#include "avbt/markets.hpp"
#include "avbt/portfolio.hpp"

namespace avbt {

// What one market charges. Costs belong to the market, not the strategy;
// ADR 0016. Fees are fractions of notional at the open and at the close
// fill. hold_long and hold_short hold one value per base bar: the cost of
// holding through that bar, as a fraction of notional, signed: positive
// pays, negative receives. NaN is zero. Empty means no holding cost.
// profit_share holds the tiers of a venue that takes a share of each
// winning close instead of size fees, such as Upside Perps; empty means
// none. ADR 0019.
struct Costs {
    double open_fee = 0.0;
    double close_fee = 0.0;
    std::vector<double> hold_long, hold_short;
    std::vector<Tier> profit_share;
};

// Throws std::invalid_argument when profit-share tiers sit beside a size
// fee, or a tier's max_roi is not positive and strictly increasing, or its
// share is outside [0, 1], or a value is not finite.
inline void check_profit_share(const Costs& c) {
    if (c.profit_share.empty()) return;
    if (c.open_fee != 0.0 || c.close_fee != 0.0) {
        throw std::invalid_argument("profit share and size fees do not mix");
    }
    double last = 0.0;
    for (const Tier& t : c.profit_share) {
        if (!std::isfinite(t.max_roi) || t.max_roi <= last) {
            throw std::invalid_argument("profit share max_roi must be positive and increasing");
        }
        if (!(t.share >= 0.0 && t.share <= 1.0)) {
            throw std::invalid_argument("profit share must be in [0, 1]");
        }
        last = t.max_roi;
    }
}

using MarketCosts = std::map<std::string, Costs>;

// Throws std::invalid_argument when a market has no Costs, a Costs names
// no market, a holding column is neither empty nor one value per base bar,
// or the profit share fails check_profit_share.
inline void check_costs(const Markets& markets, const MarketCosts& costs) {
    for (const Market& m : markets.all()) {
        auto it = costs.find(m.instrument);
        if (it == costs.end()) throw std::invalid_argument("market " + m.instrument + " has no Costs");
        check_profit_share(it->second);
        std::size_t n = m.timeframes.front().ts.size();
        for (const auto* hold : {&it->second.hold_long, &it->second.hold_short}) {
            if (!hold->empty() && hold->size() != n) {
                throw std::invalid_argument("market " + m.instrument + ": holding costs have " +
                                            std::to_string(hold->size()) + " values, base bars " +
                                            std::to_string(n));
            }
        }
    }
    for (const auto& [name, c] : costs) {
        bool known = false;
        for (const Market& m : markets.all()) known = known || m.instrument == name;
        if (!known) throw std::invalid_argument("Costs for " + name + ", which is not a market");
    }
}

}
