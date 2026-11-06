import {
  hapticFeedback,
  type ImpactHapticFeedbackStyle,
  type NotificationHapticFeedbackType,
} from '@tma.js/sdk-react';

// Each call is a no-op on clients without haptics (Mini Apps 6.1) and outside Telegram.
export const haptic = {
  impact(style: ImpactHapticFeedbackStyle = 'light'): void {
    hapticFeedback.impactOccurred.ifAvailable(style);
  },
  notification(type: NotificationHapticFeedbackType): void {
    hapticFeedback.notificationOccurred.ifAvailable(type);
  },
  selection(): void {
    hapticFeedback.selectionChanged.ifAvailable();
  },
};
