from abc import ABC, abstractmethod
from datetime import datetime


class IProfileViewRepository(ABC):
    @abstractmethod
    async def add(self, viewer_profile_id: int, viewed_profile_id: int, viewed_at: datetime) -> bool:
        """Records the first view of a profile by a viewer profile. A repeat view changes nothing.

        Returns True if this call stored the view, False if it had been stored already (also by a parallel request).
        """
