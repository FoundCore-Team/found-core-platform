from abc import ABC, abstractmethod


class INotificationJobQueue(ABC):
    @abstractmethod
    async def enqueue(self, opener_profile_id: int, opened_profile_id: int) -> None:
        """Never raises: the open is already committed, and a broker outage must not fail the request.

        A job that was not scheduled is picked up later by the sweep, because its row stays `pending`.
        """
