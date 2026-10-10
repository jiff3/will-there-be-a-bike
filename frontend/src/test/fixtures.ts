/** Synthetic, deterministic test contracts only; never imported by application pages. */
import type {
  DemandHistory,
  Forecast,
  ForecastItem,
  Recommendation,
  Station,
  TripRecommendations,
} from '../lib/types';
export function station(id = 'alpha', name = 'Alpha & First'): Station {
  const observed = new Date(Date.now() - 1000).toISOString();
  return {
    external_id: id,
    name,
    capacity: 24,
    is_demo: false,
    is_active: true,
    catalog_updated_at: observed,
    latitude: 40.748,
    longitude: -73.985,
    bikes: 5,
    docks: 18,
    reported_at: observed,
    feed_updated_at: observed,
    fetched_at: observed,
    is_installed: true,
    is_renting: true,
    is_returning: true,
    stale: false,
    freshness: 'fresh',
    distance_method: 'great_circle_times_1.3_not_pedestrian_routing',
  };
}
const item: ForecastItem = {
  mean: 2,
  interval: [0, 5],
  interval_nominal_coverage: 0.9,
  interval_method: 'scaled residual',
  interval_validated_for_this_context: false,
  model: 'seasonal',
  feature_mode: 'archive',
  confidence: 'low',
  drivers: [{ feature: 'historical_support', value: 20 }],
};
export const forecast: Forecast = {
  station_id: 'alpha',
  as_of: '2026-10-10T06:00:00Z',
  model_version: 'test-synthetic-model',
  is_demo: false,
  training_age_days: 600,
  warnings: ['unvalidated_calendar_season'],
  forecasts: {
    departures_30: item,
    departures_60: { ...item, mean: 4 },
    arrivals_30: { ...item, mean: 3 },
    arrivals_60: { ...item, mean: 6 },
  },
  availability_risk: {
    status: 'unavailable',
    reason: 'forecast_low_confidence',
    calibrated_stockout_probability: false,
    assumptions: 'Independent flows, no rebalancing; not calibrated stockout outcomes.',
  },
  target_semantics: 'Observed rides only',
};
export const history: DemandHistory = {
  station_id: 'alpha',
  start: '2025-02-28T05:00:00Z',
  end: '2025-03-01T05:00:00Z',
  observations: [
    { bucket_start: '2025-02-28T13:00:00Z', departures: 3, arrivals: 1 },
    { bucket_start: '2025-02-28T13:15:00Z', departures: 2, arrivals: 0 },
  ],
  sources: [{ id: 'test-source', source: 'synthetic test archive', checksum: 'test' }],
  is_demo: true,
  missing_bins: 'not_returned_and_not_assumed_zero',
  semantics: 'observed rides only',
};
export function recommendations(): TripRecommendations {
  const option: Recommendation = {
    ...station(),
    walking_meters: 120,
    score: 0.8,
    eligibility_tier: 0,
    estimated_usable_inventory: null,
    estimate_minutes_ahead: 0,
    mode: 'live_inventory_only',
    reasons: ['Fresh measured inventory; approximately 120 m walking, not routing.'],
    model_version: 'test-synthetic-model',
    calibrated_stockout_probability: false,
  };
  return {
    departure_time: new Date().toISOString(),
    generated_at: new Date().toISOString(),
    pickup: [option],
    destination: [{ ...option, external_id: 'beta', name: 'Beta & Second' }],
    approximate_cycling_minutes: 7,
    warnings: [],
    distance_method: 'not_routing',
    travel_time_method: 'not_routing',
    future_inventory_method: 'not_calibrated',
    preferences: { max_walk_meters: 1000, reliability_weight: 0.7 },
  };
}
