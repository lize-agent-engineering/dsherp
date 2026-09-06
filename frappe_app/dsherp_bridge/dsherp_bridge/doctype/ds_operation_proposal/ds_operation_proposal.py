import frappe
from frappe.model.document import Document


class DSOperationProposal(Document):
    def validate(self):
        previous = self.get_doc_before_save()
        if previous and any(self.get(field) != previous.get(field) for field in ("owner", "conversation", "model_run", "payload", "digest", "expires_at")):
            frappe.throw("操作提案不可修改，请重新提出操作")

    def on_trash(self):
        # Ruling #3: an audit record is an external promise. Permissions can be bypassed
        # (ignore_permissions, Administrator); this cannot.
        frappe.throw("操作提案不可删除：确认与执行都以它为准")
