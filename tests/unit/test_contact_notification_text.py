from app.domain.entities import ContactNotification
from app.domain.services import build_contact_notification_text


def make_notification(**overrides) -> ContactNotification:
    defaults = dict(
        owner_telegram_id=555,
        opener_profile_id=7,
        opener_first_name="Анна",
        opener_last_name="Иванова",
        opener_username="anna_founder",
        opener_category_title="Tech & Product",
        opener_role_title="Founder",
        owner_category_title="Tech & Product",
        owner_role_title="Engineering",
    )
    defaults.update(overrides)
    return ContactNotification(**defaults)


def test_the_text_is_the_one_from_the_plan():
    assert build_contact_notification_text(make_notification()) == (
        "Анна Иванова открыл(а) ваш контакт\n"
        "\n"
        "Анкета: Tech & Product · Founder\n"
        "Ваша анкета: Tech & Product · Engineering\n"
        "Написать: @anna_founder"
    )


def test_without_a_last_name_only_the_first_name_is_used():
    text = build_contact_notification_text(make_notification(opener_last_name=None))

    assert text.splitlines()[0] == "Анна открыл(а) ваш контакт"


def test_an_empty_last_name_is_treated_as_missing():
    text = build_contact_notification_text(make_notification(opener_last_name=""))

    assert text.splitlines()[0] == "Анна открыл(а) ваш контакт"


def test_the_two_profiles_may_be_in_different_categories():
    text = build_contact_notification_text(
        make_notification(owner_category_title="Edu & Growth", owner_role_title="Mentor / Mentee")
    )

    assert "Анкета: Tech & Product · Founder" in text
    assert "Ваша анкета: Edu & Growth · Mentor / Mentee" in text


def test_user_supplied_text_is_passed_through_untouched():
    # Plain text, no parse_mode: nothing to escape, nothing to strip.
    text = build_contact_notification_text(make_notification(opener_first_name="<b>Eve_*</b>"))

    assert text.splitlines()[0] == "<b>Eve_*</b> Иванова открыл(а) ваш контакт"
