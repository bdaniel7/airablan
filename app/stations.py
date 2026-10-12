from pathlib import Path


class StationProvider:
    def __init__(self, stations_path):
        self.path = Path(stations_path)

    def available(self):
        return self.path.exists()

    def load(self):
        return {}