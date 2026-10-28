// Tests for the tiered profit share on Costs and Fees (issue #19).
// ROI is the part's gross profit over its collateral, before holding
// costs; the fee is the tier's share of the whole profit, not marginal.
// The program returns 0 when every check passes and 1 when any check fails.

#include "avbt/backtest.hpp"
#include "avbt/costs.hpp"
#include "avbt/portfolio.hpp"

#include <cmath>
#include <cstdio>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

int failures = 0;

constexpr double kTolerance = 1e-9;

void check_value(const std::string& label, double actual, double expected) {
    if (!(std::abs(actual - expected) < kTolerance)) {
        ++failures;
        std::printf("FAIL %s: expected %f, got %f\n", label.c_str(), expected, actual);
    }
}

void check_true(const std::string& label, bool condition) {
    if (!condition) {
        ++failures;
        std::printf("FAIL %s\n", label.c_str());
    }
}

using namespace avbt;

const double kNan = std::nan("");

std::vector<Tier> upside() {
    return {{2.5, 0.25}, {5.0, 0.20}, {15.0, 0.10}, {25.0, 0.05}};
}

Fees share_fees() { return Fees{0.0, 0.0, upside()}; }

// 1% of 10,000 at a stop 5 below 100: 20 units, 2,000 notional,
// 200 collateral at 10x. ROI at exit P is 20 * (P - 100) / 200.
Portfolio long_at_100() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, kNan, share_fees());
    return p;
}

// Closes the long at `exit`; checks fee, result and balance.
void check_tier(const std::string& label, double exit, double fee) {
    Portfolio p = long_at_100();
    double gross = 20.0 * (exit - 100.0);
    auto c = p.close("BTC", exit);
    check_true(label + " closed", c.has_value());
    if (!c) return;
    check_value(label + " fee", c->fees, fee);
    check_value(label + " result", c->result, gross - fee);
    check_value(label + " balance", p.report().balance, 10000.0 + gross - fee);
}

void test_tiers() {
    check_tier("roi 10%", 101.0, 0.25 * 20.0);
    check_tier("roi 250% exact", 125.0, 0.25 * 500.0);
    check_tier("roi 255%", 125.5, 0.20 * 510.0);
    check_tier("roi 500% exact", 150.0, 0.20 * 1000.0);
    check_tier("roi 505%", 150.5, 0.10 * 1010.0);
    check_tier("roi 1500% exact", 250.0, 0.10 * 3000.0);
    check_tier("roi 1505%", 250.5, 0.05 * 3010.0);
    check_tier("roi 1600%", 260.0, 0.05 * 3200.0);
    check_tier("roi 2500% exact", 350.0, 0.05 * 5000.0);
    check_tier("roi 3000% above all", 400.0, 0.05 * 6000.0);
    check_tier("loss", 97.0, 0.0);
    check_tier("breakeven", 100.0, 0.0);
}

// Short 20 units from 100, stop 105, 200 collateral.
void test_short() {
    for (auto [exit, fee] : {std::pair{75.0, 0.25 * 500.0}, {50.0, 0.20 * 1000.0},
                             {103.0, 0.0}}) {
        Portfolio p(PortfolioSettings{});
        p.open("BTC", Side::Short, 100.0, 105.0, 10.0, kNan, share_fees());
        auto c = p.close("BTC", exit);
        double gross = 20.0 * (100.0 - exit);
        std::string l = "short exit " + std::to_string(exit);
        check_value(l + " fee", c->fees, fee);
        check_value(l + " result", c->result, gross - fee);
    }
}

// Half (10 units, 100 collateral) takes profit at 150: gross 500, ROI 5
// on the part (not 2.5 on the whole), 20%, fee 100. The rest closes at
// 260: gross 1,600 on 100 collateral, ROI 16, 5%, fee 80.
void test_partial_take_profit() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 150.0, share_fees(), 0.0, 0.5);
    auto parts = p.check({Quote{"BTC", 105.0, 152.0, 104.0, 151.0}});
    check_true("partial fired", parts.size() == 1 &&
                                    parts[0].cause == Cause::PartialTakeProfit);
    if (parts.size() != 1) return;
    check_value("partial fee", parts[0].fees, 100.0);
    check_value("partial result", parts[0].result, 400.0);
    check_value("partial collateral shrinks", p.positions().at(0).collateral, 100.0);
    auto rest = p.close("BTC", 260.0);
    check_value("rest fee", rest->fees, 80.0);
    check_value("rest result", rest->result, 1520.0);
    check_value("partial balance", p.report().balance, 11920.0);
}

// A direct half close at 125: part ROI 250% on 100 collateral, 25%.
// The rest at 150.5: 505% on 100 collateral, 10%.
void test_partial_close() {
    Portfolio p = long_at_100();
    auto a = p.close("BTC", 125.0, 0.5);
    check_value("half close fee", a->fees, 0.25 * 250.0);
    auto b = p.close("BTC", 150.5);
    check_value("second half fee", b->fees, 0.10 * 505.0);
}

void test_liquidation_pays_nothing() {
    Portfolio p = long_at_100();
    auto c = p.check({Quote{"BTC", 90.0, 90.0, 80.0, 85.0}});
    check_true("liquidated", c.size() == 1 && c[0].cause == Cause::Liquidation);
    if (c.size() != 1) return;
    // No profit share; the liquidation fee is the 30 the 170 loss leaves of 200. ADR 0023.
    check_value("liquidation fee", c[0].fees, 30.0);
    check_value("liquidation balance", p.report().balance, 9800.0);
}

// Holding is left out of the ROI. Receiving 20 at a 250% close would
// make 260% if counted (20%); it must stay 25%. Paying 20 at 255% would
// make 245% (25%); it must stay 20%.
void test_holding_keeps_tier() {
    Portfolio a = long_at_100();
    a.hold("BTC", -0.01, 100.0);
    auto ca = a.close("BTC", 125.0);
    check_value("receive keeps tier fee", ca->fees, 125.0);
    check_value("receive result", ca->result, 500.0 - 125.0 + 20.0);
    check_value("receive balance", a.report().balance, 10395.0);

    Portfolio b = long_at_100();
    b.hold("BTC", 0.01, 100.0);
    auto cb = b.close("BTC", 125.5);
    check_value("pay keeps tier fee", cb->fees, 102.0);
    check_value("pay result", cb->result, 510.0 - 102.0 - 20.0);
}

// BTC marked at 150 owes 20% of 1,000; ETH at 97 loses 60, owes nothing.
void test_equity_subtracts_owed_share() {
    Portfolio p = long_at_100();
    p.open("ETH", Side::Long, 100.0, 95.0, 10.0, kNan, share_fees());
    p.check({Quote{"BTC", 150.0, 150.0, 150.0, 150.0},
             Quote{"ETH", 97.0, 97.0, 97.0, 97.0}});
    check_value("equity after owed share", p.report().equity, 10000.0 + 800.0 - 60.0);
    check_value("balance unmoved", p.report().balance, 10000.0);

    Portfolio loser = long_at_100();
    loser.check({Quote{"BTC", 97.0, 97.0, 97.0, 97.0}});
    check_value("loser equity", loser.report().equity, 9940.0);
}

// Old model: the pending close fee is not in equity; fees as before.
void test_old_fees_unchanged() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, kNan, Fees{0.001, 0.001, {}});
    check_value("old open fee", p.report().balance, 9998.0);
    p.check({Quote{"BTC", 110.0, 110.0, 110.0, 110.0}});
    check_value("old equity", p.report().equity, 10198.0);
    auto c = p.close("BTC", 110.0);
    check_value("old fees", c->fees, 4.2);
    check_value("old result", c->result, 195.8);
}

// Hard stop at 10%: floor 9,000. BTC 40 units 100 -> 60 loses 1,600;
// GOOGL 20 units 200 -> 205 gains 100, ROI 100 / 4,000 = 2.5%, fee 25.
void test_hard_stop_charges_winner() {
    Portfolio p(PortfolioSettings{.risk_per_trade = 0.2, .hard_stop = 0.10});
    check_true("hs open BTC", p.open("BTC", Side::Long, 100.0, 50.0, 1.0, kNan, share_fees()));
    check_true("hs open GOOGL",
               p.open("GOOGL", Side::Long, 200.0, 100.0, 1.0, kNan, share_fees()));
    auto c = p.check({Quote{"BTC", 60.0, 60.0, 60.0, 60.0},
                      Quote{"GOOGL", 205.0, 205.0, 205.0, 205.0}});
    check_true("hs closes both", c.size() == 2);
    for (const Closed& x : c) {
        check_true("hs cause", x.cause == Cause::HardStop);
        if (x.instrument == "GOOGL") check_value("hs winner fee", x.fees, 25.0);
        if (x.instrument == "BTC") check_value("hs loser fee", x.fees, 0.0);
    }
    check_value("hs balance", p.report().balance, 10000.0 - 1600.0 + 75.0);
}

Bars flat_bars(const std::vector<double>& closes) {
    Bars b;
    b.timeframe = Timeframe::Hour1;
    for (std::size_t i = 0; i < closes.size(); ++i) {
        b.ts.push_back(static_cast<int64_t>(i) * 3600);
        b.open.push_back(closes[i]);
        b.high.push_back(closes[i]);
        b.low.push_back(closes[i]);
        b.close.push_back(closes[i]);
        b.minutes_with_data.push_back(60);
    }
    return b;
}

bool throws(const Costs& c) {
    Markets m = Markets::make({{"X", {flat_bars({100, 100})}}});
    try {
        check_costs(m, {{"X", c}});
    } catch (const std::invalid_argument&) {
        return true;
    }
    return false;
}

void test_validation() {
    const double inf = std::numeric_limits<double>::infinity();
    auto tiers = [](std::vector<Tier> t) { return Costs{.profit_share = t, .spread = no_spread}; };
    check_true("upside valid", !throws(tiers(upside())));
    check_true("share 0 and 1 valid", !throws(tiers({{1.0, 0.0}, {2.0, 1.0}})));
    check_true("empty valid", !throws(Costs{.open_fee = 0.001, .close_fee = 0.001, .spread = no_spread}));
    check_true("open fee with tiers",
               throws(Costs{.open_fee = 0.001, .profit_share = upside(), .spread = no_spread}));
    check_true("close fee with tiers",
               throws(Costs{.close_fee = 0.001, .profit_share = upside(), .spread = no_spread}));
    check_true("max_roi zero", throws(tiers({{0.0, 0.25}})));
    check_true("max_roi negative", throws(tiers({{-1.0, 0.25}})));
    check_true("max_roi equal", throws(tiers({{2.5, 0.25}, {2.5, 0.20}})));
    check_true("max_roi falls", throws(tiers({{5.0, 0.25}, {2.5, 0.20}})));
    check_true("share negative", throws(tiers({{2.5, -0.1}})));
    check_true("share above one", throws(tiers({{2.5, 1.1}})));
    check_true("max_roi nan", throws(tiers({{kNan, 0.25}})));
    check_true("max_roi inf", throws(tiers({{inf, 0.25}})));
    check_true("share nan", throws(tiers({{2.5, kNan}})));
    check_true("share inf", throws(tiers({{2.5, inf}})));
}

// ROI counts the fill prices (ADR 0020). An oracle exit at 150.5 is ROI
// 5.05, the 10% tier. With a 1% spread the fills are 101 and 148.995:
// ROI 10 * 47.995 / 101 = 4.75, the 20% tier.
void test_roi_after_spread() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, kNan, share_fees(), 0.0, 1.0, unknown_time, 0.01);
    const Position& pos = p.positions().at(0);
    double gross = pos.size * (148.995 - 101.0);
    check_value("spread roi", gross / pos.collateral, 10.0 * 47.995 / 101.0);
    auto c = p.close("BTC", 150.5, 1.0, Cause::Order, 0.01);
    check_value("spread roi fee", c->fees, 0.20 * gross);
    check_value("spread roi result", c->result, 0.80 * gross);
}

// Opens long at bar 1's decision, closes at bar 3's: fills at open[2]
// and open[4].
struct OpenThenClose {
    const Bars* bars = nullptr;
    void prepare(const Markets& m) { bars = &m.base("X"); }
    void update(const Markets&) {}
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>&) {
        int t = last_closed(*bars, now);
        Order o{.instrument = "X", .side = Side::Long, .stop_distance = 0.05,
                .leverage = 10.0};
        if (t == 1) return {o};
        o.kind = Order::Kind::Close;
        if (t == 3) return {o};
        return {};
    }
};
static_assert(Strategy<OpenThenClose>);

// Entry 100, exit 200: 20 units, gross 2,000 on 200 collateral, ROI 10,
// 10%, fee 200. Holding costs ride along and do not move the tier.
void test_backtest_trade_fees() {
    Bars b = flat_bars({100, 100, 100, 150, 200, 200});
    Markets m = Markets::make({{"X", {b}}});
    Costs c{.hold_long = std::vector<double>(6, 0.0001), .profit_share = upside(), .spread = no_spread};
    OpenThenClose s;
    Result r = backtest(s, m, {{"X", c}}, PortfolioSettings{});
    check_true("bt one trade", r.trades.size() == 1);
    if (r.trades.size() != 1) return;
    const Trade& t = r.trades[0];
    check_value("bt entry", t.entry_price, 100.0);
    check_value("bt exit", t.exit_price, 200.0);
    double gross = t.size * (t.exit_price - t.entry_price);
    check_value("bt gross", gross, 2000.0);
    check_value("bt fees", t.fees, 200.0);
    check_true("bt holding charged", t.holding_costs > 0.0);
    check_value("bt identity", t.result + t.fees + t.holding_costs, gross);
}

}

int main() {
    test_tiers();
    test_short();
    test_partial_take_profit();
    test_partial_close();
    test_liquidation_pays_nothing();
    test_holding_keeps_tier();
    test_equity_subtracts_owed_share();
    test_old_fees_unchanged();
    test_hard_stop_charges_winner();
    test_validation();
    test_backtest_trade_fees();
    test_roi_after_spread();
    if (failures == 0) std::printf("all profit share tests passed\n");
    return failures == 0 ? 0 : 1;
}
