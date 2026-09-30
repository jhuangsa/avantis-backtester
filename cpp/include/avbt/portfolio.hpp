#pragma once
#include <string>
#include <vector>

#include "avbt/indicators.hpp"

namespace avbt {

// Veranta liquidates a position once its loss reaches this fraction of its
// collateral (docs.veranta.xyz/trading/liquidations). See ADR 0008.
inline constexpr double liquidation_loss = 0.85;

// Veranta refuses a stop further than this fraction of collateral, so the
// stop always sits before the liquidation price.
inline constexpr double max_stop_loss = 0.80;

// Chosen once when the portfolio is made; fixed for the run.
struct PortfolioSettings {
    double starting_balance = 10000.0;
    // Fraction of the current balance lost if a trade's stop fills.
    double risk_per_trade = 0.01;
    // Fraction below the starting balance, counting unrealized profit and
    // loss, at which every position closes and trading stops for good.
    double hard_stop = 0.30;
};

// One open trade. A portfolio holds at most one per instrument.
struct Position {
    std::string instrument;
    Side side = Side::Long;
    double entry_price = 0.0;
    // Units of the instrument; notional is size * entry_price.
    double size = 0.0;
    // Balance locked by this trade: notional / leverage.
    double collateral = 0.0;
    double stop_price = 0.0;
    // Where the loss reaches liquidation_loss of the collateral.
    double liquidation_price = 0.0;
    // The last close seen for the instrument; values the unrealized result.
    double mark_price = 0.0;
};

// One instrument's bar, handed to Portfolio::check.
struct Quote {
    std::string instrument;
    double open = 0.0, high = 0.0, low = 0.0, close = 0.0;
};

// A snapshot of the account.
struct Report {
    // Realized money.
    double balance = 0.0;
    // Balance plus the unrealized result of every open position.
    double equity = 0.0;
    // Balance not locked as collateral; what a new trade can use.
    double free_cash = 0.0;
    int open_positions = 0;
    bool halted = false;
};

// The account: its balance and the positions that balance pays for.
// State is private so only these methods can change it.
class Portfolio {
public:
    explicit Portfolio(PortfolioSettings settings)
        : settings_(settings), balance_(settings.starting_balance) {}

    // Opens a position sized so a fill at the stop loses risk_per_trade of
    // the balance. Returns false, and opens nothing, when the portfolio has
    // halted, the instrument already has a position, the stop is on the
    // wrong side of the entry, leverage is not positive, the stop would lose
    // more than max_stop_loss of the collateral, or the collateral exceeds
    // the free cash.
    bool open(const std::string& instrument, Side side, double entry_price,
              double stop_price, double leverage);

    // Closes `fraction` (0 to 1] of the instrument's position at `price` and
    // returns the realized result. Returns 0 when there is no position.
    double close(const std::string& instrument, double price, double fraction = 1.0);

    // Applies one bar per instrument, in this order: each position's stop
    // (at the stop, or at the open when the bar gaps past it), then
    // liquidation (at the liquidation price, when a gap passes both), then
    // the hard stop on equity at the closes. Instruments without a quote keep
    // their last mark.
    void check(const std::vector<Quote>& quotes);

    Report report() const;

    const std::vector<Position>& positions() const { return positions_; }

private:
    double unrealized(const Position& p) const;

    PortfolioSettings settings_;
    // Realized money only; moves when a position closes.
    double balance_;
    std::vector<Position> positions_;
    // True once the hard stop fires; no trade opens after that.
    bool halted_ = false;
};

}
