from app.domain.entities import ContactNotification

# Plain text on purpose (no parse_mode): names need no escaping, and Telegram turns `@username` into a link itself.
CONTACT_NOTIFICATION_BUTTON_TEXT = "Открыть анкету"


def build_contact_notification_text(notification: ContactNotification) -> str:
    """The message the owner of a profile gets when somebody opens their contact. Russian only.

    The gender of the opener is unknown, hence "открыл(а)". The username lets the owner write first instead of
    spending their own daily limit on opening the contact back.
    """
    full_name = notification.opener_first_name
    if notification.opener_last_name:
        full_name = f"{full_name} {notification.opener_last_name}"
    return (
        f"{full_name} открыл(а) ваш контакт\n"
        "\n"
        f"Анкета: {notification.opener_category_title} · {notification.opener_role_title}\n"
        f"Ваша анкета: {notification.owner_category_title} · {notification.owner_role_title}\n"
        f"Написать: @{notification.opener_username}"
    )
