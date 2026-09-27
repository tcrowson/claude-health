"""Listing fixture: more toolkit use, so the toolkit stays inside this package."""
from toolkit import BaseWidget, WorkerThread, changed_signal


class Listing(BaseWidget):
    """A list the toolkit draws."""

    changed = changed_signal()

    def start(self):
        """Starts two workers."""
        return WorkerThread(self.changed), WorkerThread(self.changed)
