#pragma once
#include <limits>
#include <optional>
#include <span>
#include <string>
#include <string_view>

namespace avbt {

// Veranta's default half-spread per market, as a fraction of price, and
// the cap on it. A Costs with no spread uses its market's row; a market
// with no row is an error. ADR 0020.
//
// Source: Veranta's risk API (prod-api.avantisfi.com/risk/v2/spread), read
// on 2026-10-20 near 13:55 UTC with a small order (about $1,000), long and
// short, open and close. spread is the smallest of the four: the market's
// floor; the rest is the live order book. The API shows no cap, so
// max_spread is no_cap. Markets closed at the time (stocks of Korea, and
// others) and delisted pairs have no row. read_on is the date each row
// was read. Regenerate the rows with examples/veranta_spreads.py.

// max_spread of a row with no cap.
inline constexpr double no_cap = std::numeric_limits<double>::quiet_NaN();

struct SpreadRow {
    std::string_view market;  // the pair name, "FROM/TO", such as "BTC/USD"
    double spread = 0.0;
    double max_spread = 0.0;
    std::string_view read_on;
};

inline constexpr SpreadRow spread_rows[] = {
    // BEGIN ROWS
    {"ETH/USD", 0.0001, no_cap, "2026-10-20"},
    {"BTC/USD", 0.0001, no_cap, "2026-10-20"},
    {"SOL/USD", 0.0003, no_cap, "2026-10-20"},
    {"BNB/USD", 0.0003, no_cap, "2026-10-20"},
    {"ARB/USD", 0.000317628375, no_cap, "2026-10-20"},
    {"DOGE/USD", 0.0003, no_cap, "2026-10-20"},
    {"AVAX/USD", 0.0003, no_cap, "2026-10-20"},
    {"OP/USD", 0.00044731844, no_cap, "2026-10-20"},
    {"POL/USD", 0.000322851716, no_cap, "2026-10-20"},
    {"TIA/USD", 0.000416401006, no_cap, "2026-10-20"},
    {"SEI/USD", 0.0003, no_cap, "2026-10-20"},
    {"EUR/USD", 5e-05, no_cap, "2026-10-20"},
    {"USD/JPY", 5e-05, no_cap, "2026-10-20"},
    {"GBP/USD", 5e-05, no_cap, "2026-10-20"},
    {"USD/CAD", 0.0001, no_cap, "2026-10-20"},
    {"USD/CHF", 0.0001, no_cap, "2026-10-20"},
    {"USD/SEK", 0.000151758885, no_cap, "2026-10-20"},
    {"AUD/USD", 0.0001, no_cap, "2026-10-20"},
    {"NZD/USD", 0.0001, no_cap, "2026-10-20"},
    {"USD/SGD", 5e-05, no_cap, "2026-10-20"},
    {"XAG/USD", 0.0003, no_cap, "2026-10-20"},
    {"XAU/USD", 0.0001, no_cap, "2026-10-20"},
    {"WIF/USD", 0.000461929347, no_cap, "2026-10-20"},
    {"RENDER/USD", 0.000534510439, no_cap, "2026-10-20"},
    {"WLD/USD", 0.0003, no_cap, "2026-10-20"},
    {"ARKM/USD", 0.0015, no_cap, "2026-10-20"},
    {"PENDLE/USD", 0.000864664495, no_cap, "2026-10-20"},
    {"ONDO/USD", 0.0003, no_cap, "2026-10-20"},
    {"ENA/USD", 0.000315889228, no_cap, "2026-10-20"},
    {"AERO/USD", 0.00047779045, no_cap, "2026-10-20"},
    {"ETHFI/USD", 0.0003, no_cap, "2026-10-20"},
    {"JUP/USD", 0.000543549438, no_cap, "2026-10-20"},
    {"REZ/USD", 0.001757614263, no_cap, "2026-10-20"},
    {"LINK/USD", 0.0003, no_cap, "2026-10-20"},
    {"LDO/USD", 0.0003, no_cap, "2026-10-20"},
    {"NEAR/USD", 0.0003, no_cap, "2026-10-20"},
    {"INJ/USD", 0.0003, no_cap, "2026-10-20"},
    {"ZRO/USD", 0.0003, no_cap, "2026-10-20"},
    {"AAVE/USD", 0.0003, no_cap, "2026-10-20"},
    {"SUI/USD", 0.0003, no_cap, "2026-10-20"},
    {"TAO/USD", 0.0003, no_cap, "2026-10-20"},
    {"EIGEN/USD", 0.000849617672, no_cap, "2026-10-20"},
    {"CHILLGUY/USD", 0.0015, no_cap, "2026-10-20"},
    {"XRP/USD", 0.0003, no_cap, "2026-10-20"},
    {"TRUMP/USD", 0.0003, no_cap, "2026-10-20"},
    {"FARTCOIN/USD", 0.0003, no_cap, "2026-10-20"},
    {"HYPE/USD", 0.0002, no_cap, "2026-10-20"},
    {"BERA/USD", 0.001563504715, no_cap, "2026-10-20"},
    {"KAITO/USD", 0.001141904948, no_cap, "2026-10-20"},
    {"USD/CNH", 5e-05, no_cap, "2026-10-20"},
    {"PENGU/USD", 0.000629521537, no_cap, "2026-10-20"},
    {"VIRTUAL/USD", 0.000340238923, no_cap, "2026-10-20"},
    {"PUMP/USD", 0.0003, no_cap, "2026-10-20"},
    {"US500/USD", 0.0003, no_cap, "2026-10-20"},
    {"US100/USD", 0.0003, no_cap, "2026-10-20"},
    {"COIN/USD", 0.0005, no_cap, "2026-10-20"},
    {"NVDA/USD", 0.0005, no_cap, "2026-10-20"},
    {"AAPL/USD", 0.0005, no_cap, "2026-10-20"},
    {"AMZN/USD", 0.0005, no_cap, "2026-10-20"},
    {"MSFT/USD", 0.0005, no_cap, "2026-10-20"},
    {"META/USD", 0.0005, no_cap, "2026-10-20"},
    {"TSLA/USD", 0.0005, no_cap, "2026-10-20"},
    {"GOOG/USD", 0.0005, no_cap, "2026-10-20"},
    {"AVNT/USD", 0.000925800864, no_cap, "2026-10-20"},
    {"ASTER/USD", 0.0003, no_cap, "2026-10-20"},
    {"XPL/USD", 0.0003, no_cap, "2026-10-20"},
    {"HOOD/USD", 0.0005, no_cap, "2026-10-20"},
    {"ZEC/USD", 0.0003, no_cap, "2026-10-20"},
    {"XMR/USD", 0.0003, no_cap, "2026-10-20"},
    {"MON/USD", 0.0003, no_cap, "2026-10-20"},
    {"LIT/USD", 0.0003, no_cap, "2026-10-20"},
    {"WTI/USD", 0.000763786343, no_cap, "2026-10-20"},
    {"BRENT/USD", 0.00070678038, no_cap, "2026-10-20"},
    {"AMD/USD", 0.0005, no_cap, "2026-10-20"},
    {"INTC/USD", 0.0005, no_cap, "2026-10-20"},
    {"MU/USD", 0.0005, no_cap, "2026-10-20"},
    {"SNDK/USD", 0.0005, no_cap, "2026-10-20"},
    {"CBRS/USD", 0.0005, no_cap, "2026-10-20"},
    {"EWY/USD", 0.0005, no_cap, "2026-10-20"},
    {"BABA/USD", 0.0005, no_cap, "2026-10-20"},
    {"SPCX/USD", 0.0005, no_cap, "2026-10-20"},
    {"CRCL/USD", 0.0005, no_cap, "2026-10-20"},
    {"MRVL/USD", 0.0005, no_cap, "2026-10-20"},
    {"MSTR/USD", 0.0005, no_cap, "2026-10-20"},
    {"BB/USD", 0.000923076923, no_cap, "2026-10-20"},
    {"PLTR/USD", 0.0005, no_cap, "2026-10-20"},
    {"AVGO/USD", 0.0005, no_cap, "2026-10-20"},
    {"NFLX/USD", 0.0005, no_cap, "2026-10-20"},
    {"CRWV/USD", 0.0005, no_cap, "2026-10-20"},
    {"SK HYNIX/USD", 0.0005, no_cap, "2026-10-20"},
    {"ETH_UPSIDE/USD", 0.0001, no_cap, "2026-10-20"},
    {"BTC_UPSIDE/USD", 0.0001, no_cap, "2026-10-20"},
    {"SOL_UPSIDE/USD", 0.0003, no_cap, "2026-10-20"},
    {"XRP_UPSIDE/USD", 0.0003, no_cap, "2026-10-20"},
    {"HYPE_UPSIDE/USD", 0.0003, no_cap, "2026-10-20"},
    {"LLY/USD", 0.0005, no_cap, "2026-10-20"},
    {"BE/USD", 0.0005, no_cap, "2026-10-20"},
    {"LITE/USD", 0.0005, no_cap, "2026-10-20"},
    {"STRC/USD", 0.0005, no_cap, "2026-10-20"},
    {"DELL/USD", 0.0005, no_cap, "2026-10-20"},
    {"NBIS/USD", 0.0005, no_cap, "2026-10-20"},
    {"IREN/USD", 0.0005, no_cap, "2026-10-20"},
    {"ARM/USD", 0.0005, no_cap, "2026-10-20"},
    {"MRNA/USD", 0.0005, no_cap, "2026-10-20"},
    {"RKLB/USD", 0.0005, no_cap, "2026-10-20"},
    {"WDC/USD", 0.0005, no_cap, "2026-10-20"},
    {"NOW/USD", 0.000611283985, no_cap, "2026-10-20"},
    {"CASHCAT/USD", 0.001415604256, no_cap, "2026-10-20"},
    {"PONS/USD", 0.000636049455, no_cap, "2026-10-20"},
    {"VVV/USD", 0.000523389804, no_cap, "2026-10-20"},
    {"AMGN/USD", 0.000966672804, no_cap, "2026-10-20"},
    {"CSCO/USD", 0.000566037736, no_cap, "2026-10-20"},
    {"KLAC/USD", 0.0005, no_cap, "2026-10-20"},
    {"LRCX/USD", 0.001928239253, no_cap, "2026-10-20"},
    {"TXN/USD", 0.001409774436, no_cap, "2026-10-20"},
    {"PANW/USD", 0.001948224459, no_cap, "2026-10-20"},
    {"APP/USD", 0.001653986107, no_cap, "2026-10-20"},
    {"ALAB/USD", 0.004886335774, no_cap, "2026-10-20"},
    {"DDOG/USD", 0.00316075543, no_cap, "2026-10-20"},
    {"CEG/USD", 0.001502716449, no_cap, "2026-10-20"},
    {"SNPS/USD", 0.00221605711, no_cap, "2026-10-20"},
    {"CDNS/USD", 0.001833683353, no_cap, "2026-10-20"},
    {"MPWR/USD", 0.000771784833, no_cap, "2026-10-20"},
    {"FTNT/USD", 0.001367687285, no_cap, "2026-10-20"},
    {"ADBE/USD", 0.00135501355, no_cap, "2026-10-20"},
    {"INTU/USD", 0.001280579352, no_cap, "2026-10-20"},
    {"ADI/USD", 0.001674149608, no_cap, "2026-10-20"},
    {"TER/USD", 0.001518425068, no_cap, "2026-10-20"},
    {"NXPI/USD", 0.002121549803, no_cap, "2026-10-20"},
    {"SHOP/USD", 0.001857986461, no_cap, "2026-10-20"},
    {"WDAY/USD", 0.001417769376, no_cap, "2026-10-20"},
    {"ADSK/USD", 0.00133809099, no_cap, "2026-10-20"},
    {"MCHP/USD", 0.000897427375, no_cap, "2026-10-20"},
    {"BKNG/USD", 0.000722600787, no_cap, "2026-10-20"},
    {"MELI/USD", 0.001240426555, no_cap, "2026-10-20"},
    {"ISRG/USD", 0.001675051518, no_cap, "2026-10-20"},
    {"TMUS/USD", 0.000794367777, no_cap, "2026-10-20"},
    {"ABNB/USD", 0.00121838501, no_cap, "2026-10-20"},
    {"DASH/USD", 0.002175627879, no_cap, "2026-10-20"},
    {"PDD/USD", 0.001112347052, no_cap, "2026-10-20"},
    {"PYPL/USD", 0.0005, no_cap, "2026-10-20"},
    {"TTWO/USD", 0.001053141266, no_cap, "2026-10-20"},
    {"WMT/USD", 0.0005, no_cap, "2026-10-20"},
    {"GILD/USD", 0.00125072157, no_cap, "2026-10-20"},
    {"VRTX/USD", 0.001454328218, no_cap, "2026-10-20"},
    {"REGN/USD", 0.001390920123, no_cap, "2026-10-20"},
    {"LIN/USD", 0.000958007345, no_cap, "2026-10-20"},
    {"ADP/USD", 0.001409605823, no_cap, "2026-10-20"},
    {"HON/USD", 0.001272024875, no_cap, "2026-10-20"},
    {"USELESS/USD", 0.0003, no_cap, "2026-10-20"},
    {"STX/USD", 0.002106870229, no_cap, "2026-10-20"},
    {"AXON/USD", 0.001297577855, no_cap, "2026-10-20"},
    {"VST/USD", 0.002, no_cap, "2026-10-20"},
    {"ANET/USD", 0.002, no_cap, "2026-10-20"},
    {"UBER/USD", 0.0005, no_cap, "2026-10-20"},
    {"BA/USD", 0.000550516437, no_cap, "2026-10-20"},
    {"SOFI/USD", 0.000512470106, no_cap, "2026-10-20"},
    {"GRAB/USD", 0.002608695652, no_cap, "2026-10-20"},
    {"ASTS/USD", 0.00188648903, no_cap, "2026-10-20"},
    {"BOT/USD", 0.007896685037, no_cap, "2026-10-20"},
    // END ROWS
};

inline std::span<const SpreadRow> veranta_spreads() { return spread_rows; }

// The row named `instrument`, else the row named instrument + "/USD".
// Names match exactly, case included: "btc" finds no row.
inline std::optional<SpreadRow> veranta_spread(std::string_view instrument) {
    std::string usd = std::string(instrument) + "/USD";
    for (std::string_view name : {instrument, std::string_view(usd)}) {
        for (const SpreadRow& r : spread_rows) {
            if (r.market == name) return r;
        }
    }
    return std::nullopt;
}

}
