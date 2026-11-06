export const ru = {
  'common.retry': 'Повторить',
  'loading.text': 'Загрузка',
  'signInError.title': 'Не удалось войти',
  'signInError.text': 'Проверьте соединение и попробуйте ещё раз.',
  'banned.title': 'Доступ ограничен',
  'banned.reasonLabel': 'Причина',
  'banned.noReason': 'Причина не указана.',
  'notInTelegram.title': 'Откройте из Telegram',
  'notInTelegram.text': 'Это приложение работает только внутри Telegram. Откройте его через бота.',
  'home.greeting': 'Привет, {name}!',
} as const;

export type MessageKey = keyof typeof ru;
