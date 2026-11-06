import { mockTelegramEnv } from '@tma.js/sdk-react';

// Development and tests only. This file is not exported from index.ts: the app imports it
// dynamically under `import.meta.env.DEV`, so it never reaches a production bundle.

/** A raw `initData` the SDK accepts. The signature is fake: only a mocked backend sees it. */
export function createMockInitData(user: { id: number; first_name: string }): string {
  return new URLSearchParams({
    auth_date: String(Math.floor(Date.now() / 1000)),
    hash: 'mock',
    signature: 'mock',
    user: JSON.stringify(user),
  }).toString();
}

export function installMockTelegramEnv(rawInitData: string): void {
  mockTelegramEnv({
    resetPostMessage: true,
    launchParams: new URLSearchParams({
      tgWebAppData: rawInitData,
      tgWebAppVersion: '8.0',
      tgWebAppPlatform: 'tdesktop',
      tgWebAppThemeParams: JSON.stringify({
        bg_color: '#ffffff',
        text_color: '#000000',
        hint_color: '#707579',
        link_color: '#168acd',
        button_color: '#40a7e3',
        button_text_color: '#ffffff',
        secondary_bg_color: '#f4f4f5',
      }),
    }),
  });
}
