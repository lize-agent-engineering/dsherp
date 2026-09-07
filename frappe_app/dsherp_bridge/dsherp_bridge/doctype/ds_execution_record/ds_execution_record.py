import frappe
from frappe.model.document import Document


class DSExecutionRecord(Document):
    # The one thing that may change after a result is final: what became of the document it
    # produced. The business document is the user's to cancel or delete; the record of what the
    # assistant did is not, so it is marked rather than rewritten or removed.
    LATE_FIELDS = ("target_state",)

    def validate(self):
        previous = self.get_doc_before_save()
        if not previous:
            return
        moved = [field for field in self.meta.get_valid_columns()
                 if self.get(field) != previous.get(field) and field not in ("modified", "modified_by")]
        if previous.status != "Running" and set(moved) - set(self.LATE_FIELDS):
            frappe.throw("已记录的执行结果不可改写")
        if previous.status == "Running" and any(self.get(field) != previous.get(field)
                                                for field in ("owner", "proposal", "request_id")):
            frappe.throw("已记录的执行结果不可改写")

    def on_trash(self):
        # Ruling #3: an audit record is an external promise. Permissions can be bypassed
        # (ignore_permissions, Administrator); this cannot.
        frappe.throw("执行结果不可删除：它是对外可追责的事实")
