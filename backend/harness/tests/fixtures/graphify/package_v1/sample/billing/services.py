from sample.billing.models import Invoice
from sample.core.util import normalise


def issue(account, total):
    invoice = Invoice(account, total)
    invoice.save()
    return normalise(invoice.render())
