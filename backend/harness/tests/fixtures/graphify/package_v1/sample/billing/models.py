from decimal import Decimal

from sample.core.models import Account, BaseModel


class Invoice(BaseModel):
    def __init__(self, account: Account, total):
        self.account = account
        self.total = Decimal(total)

    def render(self):
        return f"{self.account.label()} {self.total}"
