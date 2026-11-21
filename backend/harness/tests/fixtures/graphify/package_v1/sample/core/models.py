import json
import os

from sample.core.util import normalise


class BaseModel:
    def save(self):
        return json.dumps(self.__dict__)


class Account(BaseModel):
    def __init__(self, name):
        self.name = normalise(name)

    def label(self):
        return os.path.basename(self.name)
