import hashlib
import frappe
from frappe.model.document import Document

class DSMembership(Document):
    def autoname(self):
        self.name = hashlib.sha256((self.enterprise + "\0" + self.platform_user).encode()).hexdigest()[:32]

    def validate(self):
        previous = self.get_doc_before_save()
        if previous and (previous.enterprise != self.enterprise or previous.platform_user != self.platform_user):
            frappe.throw("Membership identity cannot be changed; disable and create a new binding")
