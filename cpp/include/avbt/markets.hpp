#pragma once
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "avbt/indicators.hpp"
#include "avbt/states.hpp"

namespace avbt {

// One instrument's name and its bars on one or more timeframes, finest
// first. timeframes[0] is the base: orders fill at its opens, and stops and
// take profits are checked on its highs and lows. The coarser timeframes are
// for strategies to read. states is optional: labels for each minute.
struct Market {
    std::string instrument;
    std::vector<Bars> timeframes;
    std::optional<States> states = std::nullopt;
};

// One closed bar, for Markets::append.
struct Bar {
    int64_t ts = 0;
    double open = 0, high = 0, low = 0, close = 0;
    int minutes_with_data = 0;
    std::optional<double> volume = std::nullopt;
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
    // timestamps that do not rise, or has a base bar missing (two base bars
    // more than one bar apart; not checked on Month1), or has states whose three columns differ
    // in length or whose start is not a whole minute. The message names the market.
    static Markets make(std::vector<Market> markets);

    // Throws std::invalid_argument when no market has this name, or the
    // market has no bars on this timeframe.
    const Bars& at(const std::string& instrument, Timeframe tf) const;
    // The market's base bars, timeframes[0].
    const Bars& base(const std::string& instrument) const;
    // The market's states, or nullptr when it has none.
    const States* states(const std::string& instrument) const;
    const std::vector<Market>& all() const { return markets_; }

    // The clock: one entry per step, the UTC second at which the step's base bars open.
    const std::vector<int64_t>& clock() const { return clock_; }
    // The base timeframe, shared by every market.
    Timeframe timeframe() const { return markets_.front().timeframes.front().timeframe; }
    // The index of the market's base bar that opens at step t, or -1 when
    // the market has no bar then: its data has not started, or has ended.
    int bar_at(const std::string& instrument, int t) const;

    // Adds one closed bar to the market's bars on this timeframe. A base bar
    // whose ts is past the clock's end adds one clock step. Bars are only
    // appended, so bar numbers never shift and references from at() and
    // base() stay valid. Throws std::invalid_argument, naming the market,
    // when the market or timeframe does not exist, ts is not later than
    // that timeframe's last bar, volume is given when the column is empty or
    // missing when it is not, a base bar is not one bar after the last, or a base bar's ts is not on the clock yet and
    // earlier than its end (that would shift the clock's steps).
    void append(const std::string& instrument, Timeframe tf, const Bar& bar);
    // Adds one minute's labels at ts. With no states yet, they start at ts.
    // Throws std::invalid_argument, naming the market, when the market does
    // not exist, ts is not a whole minute, or ts is not exactly one minute
    // after the last row.
    void append_states(const std::string& instrument, int64_t ts, const State& state);

private:
    Markets(std::vector<Market> markets, std::vector<int64_t> clock)
        : markets_(std::move(markets)), clock_(std::move(clock)) {}
    const Market& find(const std::string& instrument) const;
    Market& find(const std::string& instrument);
    std::vector<Market> markets_;
    std::vector<int64_t> clock_;
};

}
