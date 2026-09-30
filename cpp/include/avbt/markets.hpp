#pragma once
#include <string>
#include <utility>
#include <vector>

#include "avbt/indicators.hpp"

namespace avbt {

// One instrument's name and its bars on one or more timeframes, finest
// first. timeframes[0] is the base: orders fill at its opens, and stops and
// take profits are checked on its highs and lows. The coarser timeframes are
// for strategies to read.
struct Market {
    std::string instrument;
    std::vector<Bars> timeframes;
};

// Several markets on one clock. Each market starts and ends with its own
// data. The clock is every base bar open of every market, sorted, with no
// repeats, so a market joins the run at its first bar. Every market has the
// same base timeframe, so one clock step is one base bar everywhere. Only
// make() builds a Markets, and it checks. See ADR 0011.
class Markets {
public:
    // Throws std::invalid_argument when the list is empty, two markets share
    // a name, or a market has no timeframes, has a timeframe twice or out of
    // order, has a base timeframe that differs from the first market's, or
    // has a Bars that is empty, has columns of different lengths, or has
    // timestamps that do not rise. The message names the market.
    static Markets make(std::vector<Market> markets);

    // Throws std::invalid_argument when no market has this name, or the
    // market has no bars on this timeframe.
    const Bars& at(const std::string& instrument, Timeframe tf) const;
    // The market's base bars, timeframes[0].
    const Bars& base(const std::string& instrument) const;
    const std::vector<Market>& all() const { return markets_; }

    // The clock: one entry per step, the UTC second at which the step's base bars open.
    const std::vector<int64_t>& clock() const { return clock_; }
    // The base timeframe, shared by every market.
    Timeframe timeframe() const { return markets_.front().timeframes.front().timeframe; }
    // The index of the market's base bar that opens at step t, or -1 when
    // the market has no bar then: its data has not started, or has ended.
    int bar_at(const std::string& instrument, int t) const;

private:
    Markets(std::vector<Market> markets, std::vector<int64_t> clock)
        : markets_(std::move(markets)), clock_(std::move(clock)) {}
    const Market& find(const std::string& instrument) const;
    std::vector<Market> markets_;
    std::vector<int64_t> clock_;
};

}
