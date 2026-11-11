class AdminRequiredError(Exception):
    message = "Admin access required."

    def __init__(self) -> None:
        super().__init__(self.message)


class UserNotFoundError(Exception):
    # Also raised for a user who is marked deleted.
    message = "User not found."

    def __init__(self) -> None:
        super().__init__(self.message)


class CannotBanAdminError(Exception):
    # An admin cannot ban another admin or themselves; revoke the admin role with `make_admin` first.
    message = "An administrator cannot be banned."

    def __init__(self) -> None:
        super().__init__(self.message)
