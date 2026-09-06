import frappe
from frappe.model.document import Document

class DSModelRun(Document):
    def on_trash(self):
        # Ruling #3: an audit record is an external promise. Permissions can be bypassed
        # (ignore_permissions, Administrator); this cannot.
        frappe.throw('运行记录不可删除：它是助手做过什么的凭据')
