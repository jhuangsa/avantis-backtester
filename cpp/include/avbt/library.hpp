#pragma once
#include <vector>

#include "avbt/registry.hpp"

// The strategy library: one file per strategy in library/. Each file holds
// the strategy and a detail::<name>_info() that names its params. To add a
// strategy, add its file, include it here, and list its info in library().
#include "avbt/library/band_scalper.hpp"
#include "avbt/library/dip_basket.hpp"
#include "avbt/library/dip_buyer.hpp"
#include "avbt/library/failed_breakout_fade.hpp"
#include "avbt/library/false_break_1h.hpp"
#include "avbt/library/range_basket.hpp"
#include "avbt/library/range_seller.hpp"
#include "avbt/library/rsi_snap.hpp"
#include "avbt/library/weekend_basket.hpp"
#include "avbt/library/weekend_breakout_fade.hpp"

namespace avbt {

inline std::vector<StrategyInfo> library() {
    return {
        detail::band_scalper_info(),
        detail::dip_basket_info(),
        detail::dip_buyer_info(),
        detail::failed_breakout_fade_info(),
        detail::false_break_1h_info(),
        detail::range_basket_info(),
        detail::range_seller_info(),
        detail::rsi_snap_info(),
        detail::weekend_basket_info(),
        detail::weekend_breakout_fade_info(),
    };
}

}
