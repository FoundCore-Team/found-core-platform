import { t } from '../../../shared/i18n';
import { Screen, Skeleton } from '../../../shared/ui';
import styles from './Screens.module.css';

export function LoadingScreen() {
  return (
    <Screen>
      <p role="status" className={styles.visuallyHidden}>
        {t('loading.text')}
      </p>
      <Skeleton variant="title" />
      <Skeleton variant="line" />
      <Skeleton variant="line" />
    </Screen>
  );
}
