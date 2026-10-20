#pragma once
#include <cmath>
#include <cstdint>
#include <limits>
#include <optional>
#include <stdexcept>
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

// Position::entry_time when the entry time is not known. 0 is a real time.
inline constexpr int64_t unknown_time = std::numeric_limits<int64_t>::min();

// Chosen once when the portfolio is made; fixed for the run.
struct PortfolioSettings {
    double starting_balance = 10000.0;
    // Fraction of the current balance lost if a trade's stop fills.
    double risk_per_trade = 0.01;
    // Fraction below the starting balance, counting unrealized profit and
    // loss, at which every position closes and trading stops for good.
    double hard_stop = 0.30;
    // When true, a trade risks risk_per_trade * leverage of the balance, so
    // 5x leverage makes and loses 5 times as much as 1x. ADR 0015.
    bool scale_risk_with_leverage = false;
    // Seconds back from now at which a strategy reads state labels; a whole
    // number of minutes, at least 60, for labels that arrive late. ADR 0012.
    int64_t state_delay = 60;
};

// One profit-share tier: closes with ROI up to max_roi pay share of the
// profit. ROI is profit over collateral, a fraction: 2.5 is 250%. ADR 0019.
struct Tier {
    double max_roi = 0.0;
    double share = 0.0;
};

// The profit share owed on a close: 0 for a loss, else the share of the
// first tier whose max_roi reaches the ROI (the last tier above them all),
// times the whole profit. Empty tiers owe nothing.
double profit_share(const std::vector<Tier>& tiers, double gross, double collateral);

// Fractions of notional charged at the open fill and at the close fill, and
// the profit-share tiers; a market uses one model or the other. ADR 0016, 0019.
struct Fees {
    double open = 0.0;
    double close = 0.0;
    std::vector<Tier> profit_share;
};

// One open trade. A portfolio holds at most one per instrument.
struct Position {
    std::string instrument;
    Side side = Side::Long;
    // The fill price, after the spread.
    double entry_price = 0.0;
    // Units of the instrument; notional is size * entry_price.
    double size = 0.0;
    // Balance locked by this trade: notional / leverage.
    double collateral = 0.0;
    double stop_price = 0.0;
    // NaN when the position has no take profit.
    double take_profit_price = 0.0;
    Fees fees;
    double leverage = 1.0;
    // Holding costs so far, account money; positive means the position pays.
    double holding = 0.0;
    // Where the oracle loss reaches liquidation_loss of the collateral.
    double liquidation_price = 0.0;
    // The last oracle close seen for the instrument; values the unrealized result.
    double mark_price = 0.0;
    // Price gap the stop keeps behind the best high (low for a short); 0 for
    // a stop that never moves.
    double trail = 0.0;
    // True once the trail has moved the stop; only then is a stop-out a TrailingStop.
    bool trailed = false;
    // Fraction of the position the take profit closes. A partial take profit
    // fires once: the take profit becomes NaN and the rest runs on its stop.
    double take_profit_fraction = 1.0;
    // UTC seconds of the entry fill; unknown_time when unknown. A strategy
    // restarted live reads it to find the signal bar of a time exit.
    int64_t entry_time = unknown_time;
    // The last half-spread seen for the instrument: the entry bar's, then
    // each quote's. The hard stop closes with it. ADR 0020.
    double spread = 0.0;
    // The spread paid at the entry fill on the size still open, account money.
    double spread_cost = 0.0;
};

// What closed a position. Order is a strategy's close order; EndOfData is
// the backtest closing it because its market's data ended. TrailingStop is
// a stop that trails; PartialTakeProfit is a take profit that closes part.
enum class Cause { Stop, TakeProfit, Liquidation, HardStop, Order, EndOfData,
                   TrailingStop, PartialTakeProfit };

// A position, or part of one, that has closed.
struct Closed {
    std::string instrument;
    Side side = Side::Long;
    // Fill prices, after the spread.
    double entry_price = 0.0;
    double exit_price = 0.0;
    double size = 0.0;
    double leverage = 1.0;
    // Open, close and profit-share fee on this size, and its share of the holding costs.
    double fees = 0.0;
    double holding = 0.0;
    // Spread paid at both fills on this size; already inside the price
    // result. ADR 0020.
    double spread_cost = 0.0;
    // Price result minus fees and holding costs.
    double result = 0.0;
    Cause cause = Cause::Order;
};

// One instrument's bar, handed to Portfolio::check, with the half-spread
// its fills pay. ADR 0020.
struct Quote {
    std::string instrument;
    double open = 0.0, high = 0.0, low = 0.0, close = 0.0;
    double spread = 0.0;
};

// A snapshot of the account.
struct Report {
    // Realized money.
    double balance = 0.0;
    // Balance plus the unrealized result of every open position, less the
    // profit share each would owe if closed at its mark.
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
    // the balance (times leverage with scale_risk_with_leverage); the fee is not part of the sizing. The open fee comes out
    // of the balance. entry_price is the oracle price; the fill is
    // entry_price * (1 + spread) for a long, (1 - spread) for a short. The
    // stop, take profit and trail stay on oracle prices; the size counts
    // the spread at both fills, the entry bar's spread at the stop. ADR 0020. A NaN take profit means none: NaN, not std::optional,
    // because the indicators already use NaN for "no value" and Position
    // stays a plain struct of doubles. Returns false, and opens nothing,
    // when the portfolio has halted, the instrument already has a position,
    // the stop or take profit is on the wrong side of the entry, leverage is
    // not positive, the stop would lose more than max_stop_loss of the
    // collateral, or the collateral exceeds the free cash, the trail is
    // negative, or take_profit_fraction is not in (0, 1].
    bool open(const std::string& instrument, Side side, double entry_price,
              double stop_price, double leverage,
              double take_profit_price = std::nan(""), Fees fees = {},
              double trail = 0.0, double take_profit_fraction = 1.0,
              int64_t entry_time = unknown_time, double spread = 0.0);

    // Adds rate * size * price to the position's holding costs. NaN adds
    // nothing; no position does nothing.
    void hold(const std::string& instrument, double rate, double price);

    // Closes `fraction` (0 to 1] of the instrument's position at the oracle
    // `price` moved against it by `spread`, charges the close fee, the profit share on that part's own ROI, and that
    // fraction of the holding costs, and returns what closed. Returns nothing when
    // there is no position. A close (full or partial) whose fill is at or past
    // the liquidation price liquidates the whole position there instead, with
    // no spread and cause Liquidation. ADR 0020.
    std::optional<Closed> close(const std::string& instrument, double price,
                                double fraction = 1.0, Cause cause = Cause::Order,
                                double spread = 0.0);

    // Applies one bar per instrument, in this order: each position's stop
    // and take profit (at the level, or at the open when the bar gaps past
    // it; the stop wins when the bar reaches both, ADR 0003), then
    // liquidation (at the liquidation price, when a gap passes both), then
    // the hard stop on equity at the closes. A position the bar left open
    // then trails its stop toward the bar's best price, never back, so the
    // new stop counts from the next bar. Instruments without a quote keep
    // their last mark. Levels trigger on the oracle prices; fills pay the
    // quote's spread, except liquidation; the hard stop pays each
    // position's last spread. Returns every position it closed.
    std::vector<Closed> check(const std::vector<Quote>& quotes);

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
