const SCENARIOS = ['ok', 'new', 'banned', 'error'] as const;

export type MockScenario = (typeof SCENARIOS)[number];

/** Reads `?mock=` from the URL; an unknown or missing value means `ok`. */
export function readScenario(search: string): MockScenario {
  const value = new URLSearchParams(search).get('mock');
  return SCENARIOS.find((scenario) => scenario === value) ?? 'ok';
}
