#pragma once
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

#include "avbt/markets.hpp"

namespace avbt {

// What one market charges. Costs belong to the market, not the strategy;
// ADR 0016. Fees are fractions of notional at the open and at the close
// fill. hold_long and hold_short hold one value per base bar: the cost of
// holding through that bar, as a fraction of notional, signed: positive
// pays, negative receives. NaN is zero. Empty means no holding cost.
struct Costs {
    double open_fee = 0.0;
    double close_fee = 0.0;
    std::vector<double> hold_long, hold_short;
};

using MarketCosts = std::map<std::string, Costs>;

// Throws std::invalid_argument when a market has no Costs, a Costs names
// no market, or a holding column is neither empty nor one value per base bar.
inline void check_costs(const Markets& markets, const MarketCosts& costs) {
    for (const Market& m : markets.all()) {
        auto it = costs.find(m.instrument);
        if (it == costs.end()) throw std::invalid_argument("market " + m.instrument + " has no Costs");
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
