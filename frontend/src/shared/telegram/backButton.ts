import { backButton as sdkBackButton } from '@tma.js/sdk-react';

const noop = (): void => undefined;

export function mountBackButton(): void {
  sdkBackButton.mount.ifAvailable();
}

// Every method checks the Mini Apps version and does nothing when the button is unavailable
// (an old client or a launch outside Telegram).
export const backButton = {
  show(): void {
    sdkBackButton.show.ifAvailable();
  },
  hide(): void {
    sdkBackButton.hide.ifAvailable();
  },
  onClick(listener: VoidFunction): VoidFunction {
    const result = sdkBackButton.onClick.ifAvailable(listener);
    return result.ok ? result.data : noop;
  },
};
