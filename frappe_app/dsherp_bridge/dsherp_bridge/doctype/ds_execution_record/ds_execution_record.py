import frappe
from frappe.model.document import Document


class DSExecutionRecord(Document):
    def validate(self):
        previous = self.get_doc_before_save()
        if previous and (previous.status != "Running" or any(self.get(field) != previous.get(field) for field in ("owner", "proposal", "request_id"))):
            frappe.throw("已记录的执行结果不可改写")
