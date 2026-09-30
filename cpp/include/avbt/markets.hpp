#pragma once
#include <string>
#include <utility>
#include <vector>

#include "avbt/indicators.hpp"

namespace avbt {

// One instrument's name and its bars.
struct Market {
    std::string instrument;
    Bars bars;
};

// Several markets on one clock: the same bar size, the same number of bars,
// and the same timestamps, so bar t is the same moment in every market. The
// caller aligns them; only make() builds a Markets, and it checks. See ADR 0010.
class Markets {
public:
    // Throws std::invalid_argument when the list is empty, two markets share
    // a name, a market's columns differ in length, or a market is not on the
    // first market's clock. The message names the market.
    static Markets make(std::vector<Market> markets);

    // Throws std::invalid_argument when no market has this name.
    const Bars& at(const std::string& instrument) const;
    const std::vector<Market>& all() const { return markets_; }
    // Bars per market.
    int size() const { return static_cast<int>(markets_.front().bars.ts.size()); }
    int bar_size_seconds() const { return markets_.front().bars.bar_size_seconds; }

private:
    explicit Markets(std::vector<Market> markets) : markets_(std::move(markets)) {}
    std::vector<Market> markets_;
};

}
