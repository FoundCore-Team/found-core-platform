import { t } from '../../../shared/i18n';
import { Screen } from '../../../shared/ui';
import styles from './Screens.module.css';

export function NotInTelegramScreen() {
  return (
    <Screen centered>
      <h1 className={styles.title}>{t('notInTelegram.title')}</h1>
      <p className={styles.text}>{t('notInTelegram.text')}</p>
    </Screen>
  );
}
