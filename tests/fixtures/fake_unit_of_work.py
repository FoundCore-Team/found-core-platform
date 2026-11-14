from app.domain.interfaces import IUnitOfWork


class FakeUnitOfWork(IUnitOfWork):
    """Counts commits so unit tests can assert that a use case committed exactly
    once on success and never on failure."""

    def __init__(self) -> None:
        self.commits = 0
        # Set by a test to play a commit that fails (a lost connection, a constraint checked at commit time).
        self.fail_on_commit = False

    async def commit(self) -> None:
        if self.fail_on_commit:
            raise RuntimeError("commit failed")
        self.commits += 1
