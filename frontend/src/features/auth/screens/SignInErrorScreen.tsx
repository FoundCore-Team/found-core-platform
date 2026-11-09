import { t } from '../../../shared/i18n';
import { Button, Screen } from '../../../shared/ui';
import styles from './Screens.module.css';

interface SignInErrorScreenProps {
  onRetry: () => void;
}

export function SignInErrorScreen({ onRetry }: SignInErrorScreenProps) {
  return (
    <Screen centered>
      <h1 className={styles.title}>{t('signInError.title')}</h1>
      <p className={styles.text}>{t('signInError.text')}</p>
      <div>
        <Button onClick={onRetry}>{t('common.retry')}</Button>
      </div>
    </Screen>
  );
}
