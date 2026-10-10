/** Transport contracts from /api/v1 OpenAPI. No browser-side forecasting. */
export interface Station {
  external_id: string;
  name: string;
  capacity: number | null;
  is_demo: boolean;
  is_active: boolean;
  catalog_updated_at: string;
  latitude: number;
  longitude: number;
  bikes: number | null;
  docks: number | null;
  reported_at: string | null;
  feed_updated_at: string | null;
  fetched_at: string | null;
  is_installed: boolean | null;
  is_renting: boolean | null;
  is_returning: boolean | null;
  stale: boolean;
  freshness: 'fresh' | 'stale' | 'missing';
  distance_method: string;
}
export interface StationPage {
  items: Station[];
  limit: number;
  offset: number;
  next_offset: number | null;
}
export interface Point {
  latitude: number;
  longitude: number;
}
export interface Place extends Point {
  label: string;
  station_id?: string;
}
export interface Preferences {
  max_walk_meters: number;
  reliability_weight: number;
}
export interface User {
  id: string;
  email: string;
  is_admin?: boolean;
}
export interface Session {
  user: User;
  csrf_token: string;
}
export type ForecastKey = 'departures_30' | 'departures_60' | 'arrivals_30' | 'arrivals_60';
export interface ForecastItem {
  mean: number;
  interval: [number, number];
  interval_nominal_coverage: number;
  interval_method: string;
  interval_validated_for_this_context: boolean;
  model: string;
  feature_mode: string;
  confidence: string;
  drivers: { feature: string; value: number | null; log_mean_contribution?: number }[];
}
export interface RiskHorizon {
  scenario_empty_at_end: number;
  scenario_full_at_end: number;
  scenario_empty_at_any_time: number;
  scenario_full_at_any_time: number;
  expected_bikes_at_end: number;
  bikes_at_end_p10: number;
  bikes_at_end_p90: number;
}
export interface Forecast {
  station_id: string;
  as_of: string;
  model_version: string;
  is_demo: boolean;
  training_age_days: number;
  warnings: string[];
  forecasts: Record<ForecastKey, ForecastItem>;
  availability_risk: {
    status: 'unavailable' | 'estimated_scenario';
    reason?: string;
    assumptions: string;
    inventory_reported_at?: string;
    horizons?: Record<string, RiskHorizon>;
    calibrated_stockout_probability: false;
  };
  target_semantics: string;
}
export interface ModelCard {
  version: string;
  created_at: string;
  is_demo: boolean;
  source: { first_bucket: number; last_bucket: number; station_count: number; type: string };
  config: { test_end: string };
}
export interface DemandPoint {
  bucket_start: string;
  departures: number;
  arrivals: number;
}
export interface DemandHistory {
  station_id: string;
  start: string;
  end: string;
  observations: DemandPoint[];
  sources: { id: string; source: string; checksum: string }[];
  is_demo: boolean;
  missing_bins: string;
  semantics: string;
}
export interface Recommendation extends Omit<
  Station,
  'is_active' | 'catalog_updated_at' | 'freshness'
> {
  walking_meters: number;
  score: number;
  eligibility_tier: number;
  estimated_usable_inventory: number | null;
  estimate_minutes_ahead: number;
  mode: 'uncalibrated_inventory_scenario' | 'live_inventory_only' | 'distance_only';
  reasons: string[];
  model_version: string | null;
  calibrated_stockout_probability: false;
}
export interface TripRequest {
  origin: Point;
  destination: Point;
  departure_time: string;
  preferences: Preferences;
  limit: number;
}
export interface TripRecommendations {
  departure_time: string;
  generated_at: string;
  pickup: Recommendation[];
  destination: Recommendation[];
  approximate_cycling_minutes: number;
  warnings: string[];
  distance_method: string;
  travel_time_method: string;
  future_inventory_method: string;
  preferences: Preferences;
}

export interface CommuteInput {
  name: string;
  origin: Place;
  destination: Place;
  departure_local: string;
  timezone: string;
  weekdays: number[];
  preferences: Preferences;
}
export interface SavedCommute extends CommuteInput {
  id: string;
  created_at: string;
  updated_at: string;
  next_departure: string;
}
export interface AlertInput {
  station_id: string;
  kind: 'pickup_empty' | 'destination_full';
  mode: 'inventory' | 'estimated_risk';
  threshold: number;
  lead_minutes: 30 | 60;
  cooldown_minutes: number;
  enabled: boolean;
}
export interface AvailabilityAlert extends AlertInput {
  id: string;
  commute_id: string;
  station_name: string;
  latitude: number;
  longitude: number;
  last_checked_at: string | null;
  last_status: string;
}
export interface RiderNotification {
  id: string;
  title: string;
  message: string;
  created_at: string;
  scheduled_for: string;
  read_at: string | null;
  evidence: {
    mode: 'inventory' | 'estimated_risk';
    station_id: string;
    station_name: string;
    reported_at: string;
    commute_id: string;
    model_version?: string;
    issuance?: string;
  };
}
export interface Inbox {
  items: RiderNotification[];
  unread_count: number;
  next_offset: number | null;
}
export type MapStation = Pick<Station, 'external_id' | 'name' | 'latitude' | 'longitude'>;
export interface ReplayCatalog {
  dataset: null | {
    id: string;
    source_sha256: string;
    source_label: string;
    is_demo?: boolean;
    row_count: number;
    created_at: string;
  };
  days: string[];
  timezone: string;
}
export interface ReplayDay {
  dataset_id: string | null;
  buckets: string[];
  stations: MapStation[];
  series: { station_id: string; departures: (number | null)[]; arrivals: (number | null)[] }[];
}
export interface ReplayComparison {
  as_of: string;
  model_version: string;
  computed_at: string;
  forecasts: Record<ForecastKey, ForecastItem>;
  observed: Record<ForecastKey, number | null>;
}
