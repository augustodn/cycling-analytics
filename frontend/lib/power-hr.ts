export const PERIODS = ["7d", "21d", "28d", "30d", "42d", "90d", "365d", "all", "custom"] as const;
export const HR_ZONE_COLORS = ["#808080", "#87CEEB", "#228B22", "#FFD700", "#FF69B4", "#FF0000", "#8A2BE2"] as const;
export type SearchParams = Record<string, string | string[] | undefined>;
export type HRPoint = {
  hr_median_bpm: number | null;
  hr_p25_bpm: number | null;
  hr_p75_bpm: number | null;
  rolling_median_hr_bpm?: number | null;
  valid_seconds: number;
  efficiency_w_per_bpm?: number | null;
  activity_count?: number;
  status?: string;
  confidence_reasons?: string[];
};
export type PowerHRGroup = {
  group_id: string;
  kind: "all" | "work_band" | "elapsed_split";
  start_kj?: number;
  end_kj?: number | null;
  bins: Array<HRPoint & { power_w: number; power_low_w: number; power_high_w: number }>;
};
export type PowerHRActivity = {
  activity_id: string;
  date: string;
  duration_s: number;
  modality: string;
  effective_ftp_w: number | null;
  total_work_kj?: number | null;
  hr_zone_bounds: number[] | null;
  available?: boolean;
  reason?: string | null;
  parameter_warning?: string | null;
  work_complete?: boolean;
  context?: { source_name?: string; avg_power_w?: number | null; avg_hr_bpm?: number | null; temperature_c?: number | null };
  analysis?: { groups: PowerHRGroup[]; fatigue_unclassified_seconds?: number } | null;
};
export type PowerHRBucket = {
  groups: PowerHRGroup[];
  activities: Record<string, PowerHRActivity>;
  hr_at_fixed_power: Array<HRPoint & { activity_id: string; date: string; target_power_w: number; matched_power_median_w: number | null }>;
};
export type PowerHRPeriod = {
  selection?: { activity_id?: string; period?: string; start_date?: string | null; end_date?: string | null };
  environments: Partial<Record<"indoor" | "outdoor" | "unknown", PowerHRBucket>>;
};
export type PowerHRData = { current_period: PowerHRPeriod; comparison_period?: PowerHRPeriod | null; caveat?: string };

// Shared by native inputs and server validation: [name, label, default, min, max, step].
export const NUMBER_CONTROLS = [
  ["lag_s", "HR lag (s)", 30, 15, 60, 1],
  ["power_window_s", "Power smoothing (s)", 30, 1, 3600, 1],
  ["hr_window_s", "HR smoothing (s)", 30, 1, 3600, 1],
  ["bin_size_w", "Power bin width (W)", 10, 0.1, 1000, 0.1],
  ["stable_window_s", "Stable window (s)", 180, 1, 3600, 1],
  ["max_power_cv", "Maximum power CV", 0.08, 0, undefined, 0.01],
  ["min_power_ftp_fraction", "Stable power floor (FTP fraction)", 0.4, 0, undefined, 0.01],
  ["min_cadence_rpm", "Stable cadence floor (rpm)", 50, 0, undefined, 1],
  ["aerobic_floor_ftp_fraction", "Optional aerobic floor (FTP fraction)", "", 0, undefined, 0.01],
  ["aerobic_ceiling_ftp_fraction", "Optional aerobic ceiling (FTP fraction)", "", 0, undefined, 0.01],
  ["min_activities", "Minimum activities per bin", 3, 1, undefined, 1],
  ["min_total_seconds", "Minimum bin exposure (s)", 300, 0, undefined, 1],
  ["target_power_tolerance_w", "Fixed-power tolerance (± W)", 5, 0, 100, 0.1],
  ["target_power_min_seconds", "Minimum fixed-power exposure (s)", 60, 0, undefined, 1],
] as const;

export function queryValue(params: SearchParams, name: string, fallback = "") {
  const value = params[name];
  return (Array.isArray(value) ? value[0] : value) ?? fallback;
}

export function powerHRRequest(params: SearchParams) {
  const choice = (name: string, options: readonly string[], fallback: string) => {
    const value = queryValue(params, name, fallback);
    if (!options.includes(value)) throw new Error(`Invalid ${name}.`);
    return value;
  };
  const view = choice("view", ["individual", "period", "compare"], "period");
  const grouping = choice("grouping", ["all", "fatigue", "half", "third"], "all");
  const period = (prefix = "") => {
    const selected = choice(`${prefix}period`, PERIODS, "90d");
    if (selected !== "custom") return { period: selected };
    const start = queryValue(params, `${prefix}start_date`);
    const end = queryValue(params, `${prefix}end_date`);
    const validDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
    if (!validDate(start) || !validDate(end) || start > end) throw new Error("Custom ranges require valid, ordered start and end dates.");
    return { period: selected, start_date: start, end_date: end };
  };
  const request: Record<string, unknown> = {
    environment: choice("environment", ["indoor", "outdoor", "both"], "both"),
    parameter_mode: choice("parameter_mode", ["historical", "current"], "historical"),
    mode: choice("mode", ["observed", "stable"], "observed"),
  };
  if (view === "individual") {
    const id = queryValue(params, "activity_id").trim();
    if (!id || id.length > 128) throw new Error("Select an activity for the individual view.");
    request.activity_id = id;
  } else {
    Object.assign(request, period());
    if (view === "compare") request.compare_period = period("compare_");
  }
  for (const [name, label, fallback, min, max, step] of NUMBER_CONTROLS) {
    const raw = queryValue(params, name, String(name === "min_activities" && view === "individual" ? 1 : fallback));
    if (raw === "" && fallback === "") continue;
    const value = Number(raw);
    if (raw.trim() === "" || !Number.isFinite(value) || value < min || (max !== undefined && value > max) || (step === 1 && !Number.isSafeInteger(value))) throw new Error(`Invalid ${label.toLowerCase()}.`);
    request[name] = value;
  }
  const list = (name: string, fallback: string, integer = false) => {
    const raw = queryValue(params, name, fallback).trim();
    const values = raw === "" ? [] : raw.split(",").map((item) => item.trim() === "" ? NaN : Number(item));
    if (values.length > 20 || values.some((value) => !Number.isFinite(value) || value <= 0 || (integer && (!Number.isInteger(value) || value > 1000)))) throw new Error(`Invalid ${name}: use up to 20 positive comma-separated numbers.`);
    return values;
  };
  if (grouping === "fatigue") {
    const thresholds = list("fatigue_thresholds_kj", "500,1000,1500");
    if (!thresholds.length || thresholds.some((value, index) => index > 0 && value <= thresholds[index - 1])) throw new Error("Fatigue thresholds must be positive and strictly increasing.");
    request.fatigue_thresholds_kj = thresholds;
  }
  if (grouping === "half" || grouping === "third") request.elapsed_splits = grouping === "half" ? 2 : 3;
  const targets = list("target_power_w", "180,190,200,210,220,240", true);
  if (new Set(targets).size !== targets.length) throw new Error("Fixed-power targets must be unique.");
  request.target_power_w = targets;
  const floor = Math.max(Number(request.aerobic_floor_ftp_fraction ?? 0), request.mode === "stable" ? Number(request.min_power_ftp_fraction) : 0);
  if (request.aerobic_ceiling_ftp_fraction !== undefined && Number(request.aerobic_ceiling_ftp_fraction) < floor) throw new Error("Aerobic ceiling must be at least the effective power floor.");
  return { request, view, grouping };
}

export function selectedGroups(groups: PowerHRGroup[], grouping: string) {
  return groups.filter((group) => group.kind === (grouping === "fatigue" ? "work_band" : grouping === "all" ? "all" : "elapsed_split"));
}

export function deltaPowerHRGroups(primary: PowerHRGroup[], baseline: PowerHRGroup[], grouping: string) {
  const baselineById = new Map(selectedGroups(baseline, grouping).map((group) => [group.group_id, group]));
  return selectedGroups(primary, grouping).flatMap((group) => {
    const reference = baselineById.get(group.group_id);
    if (!reference) return [];
    const referenceBins = new Map(reference.bins.map((bin) => [bin.power_low_w, bin]));
    return group.bins.flatMap((bin) => {
      const comparison = referenceBins.get(bin.power_low_w);
      if (!comparison || bin.status !== "ok" || comparison.status !== "ok" || bin.hr_median_bpm === null || comparison.hr_median_bpm === null) return [];
      return [{
        curve: groupLabel(group),
        power: `${bin.power_low_w}–${bin.power_high_w} W`,
        primary_hr: bin.hr_median_bpm,
        baseline_hr: comparison.hr_median_bpm,
        delta_hr: bin.hr_median_bpm - comparison.hr_median_bpm,
        primary_activities: bin.activity_count ?? 1,
        baseline_activities: comparison.activity_count ?? 1,
      }];
    });
  });
}

export function groupLabel(group: PowerHRGroup) {
  if (group.kind === "all") return "All eligible samples";
  if (group.kind === "work_band") return `${group.start_kj ?? 0}–${group.end_kj ?? "∞"} kJ`;
  const match = group.group_id.match(/elapsed_(\d+)_of_(\d+)/);
  return match ? `Part ${match[1]} of ${match[2]}` : group.group_id;
}

export function zoneReference(activities: PowerHRActivity[]) {
  const reference = activities.filter((activity) => activity.hr_zone_bounds?.length && activity.hr_zone_bounds.every((bound, index, bounds) => Number.isFinite(bound) && bound > 0 && (index === 0 || bound > bounds[index - 1])))
    .sort((a, b) => b.date.localeCompare(a.date))[0];
  return {
    reference,
    differs: Boolean(reference && activities.some((activity) => JSON.stringify(activity.hr_zone_bounds) !== JSON.stringify(reference.hr_zone_bounds))),
  };
}
