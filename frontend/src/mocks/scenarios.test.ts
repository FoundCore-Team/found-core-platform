import { describe, expect, it } from 'vitest';

import { readScenario } from './scenarios';

describe('readScenario', () => {
  it.each([['ok'], ['new'], ['banned'], ['error']])('reads ?mock=%s', (scenario) => {
    expect(readScenario(`?mock=${scenario}`)).toBe(scenario);
  });

  it.each([[''], ['?'], ['?mock='], ['?mock=unknown'], ['?other=banned']])(
    'falls back to ok for %j',
    (search) => {
      expect(readScenario(search)).toBe('ok');
    },
  );
});
