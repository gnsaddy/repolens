# Fix NaN in the tokens-per-day chart; point to where prices are set

### Fixed
- Insights → "Tokens per day" showed `NaN` and drew nothing: the chart added `input_tokens + output_tokens`, but the server sends one `tokens` figure per day. The chart now uses it (and still accepts the older split shape); a missing figure counts as zero, never NaN. The UI type for this series was wrong and is corrected.

### Changed
- The "Costs show $0.00" notice now says where to fix it: **Settings → AI & search** (price per 1M tokens for each model), with `REPOLENS_PRICING_JSON` as the server-side alternative, and notes prices apply to new usage only. Insights' Spend card links to the same place.
