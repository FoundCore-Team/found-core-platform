import { t } from '../../../shared/i18n';
import { Screen } from '../../../shared/ui';
import styles from './Screens.module.css';

interface BannedScreenProps {
  reason: string | null;
}

export function BannedScreen({ reason }: BannedScreenProps) {
  return (
    <Screen centered>
      <h1 className={styles.title}>{t('banned.title')}</h1>
      <p className={styles.text}>
        {reason === null || reason === '' ? (
          t('banned.noReason')
        ) : (
          <>
            <strong>{t('banned.reasonLabel')}:</strong>{' '}
            <span className={styles.reason}>{reason}</span>
          </>
        )}
      </p>
    </Screen>
  );
}
