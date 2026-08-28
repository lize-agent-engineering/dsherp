import frappe
from frappe.model.document import Document


class DSOperationProposal(Document):
    def validate(self):
        previous = self.get_doc_before_save()
        if previous and any(self.get(field) != previous.get(field) for field in ("owner", "conversation", "payload", "digest", "expires_at")):
            frappe.throw("操作提案不可修改，请重新提出操作")
