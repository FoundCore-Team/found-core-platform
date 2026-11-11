class ReportProfileNotFoundError(Exception):
    # Also raised for an own, paused, hidden or otherwise invisible profile: the same answer for all of them, so ids
    # cannot be probed to find out which profiles exist.
    message = "Profile not found."

    def __init__(self) -> None:
        super().__init__(self.message)
