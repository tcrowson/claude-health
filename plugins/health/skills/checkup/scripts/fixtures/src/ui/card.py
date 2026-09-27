"""Card fixture: overrides a toolkit callback and uses the toolkit's worker and signal types."""
from src.core import helpers
from toolkit import BaseWidget, WorkerThread, changed_signal


class Card(BaseWidget):
    """A card the toolkit draws."""

    updated = changed_signal()

    def drawEvent(self, ctx):
        """Called by the toolkit to draw."""
        return helpers.combine([1], [2], 0.5)

    def refresh(self):
        """Starts a worker."""
        return WorkerThread(self.updated)
