import frappe
from frappe.model.document import Document


class DSRunEvent(Document):
    def autoname(self):
        self.name = f"{self.run}-{int(self.seq):06d}"

    def validate(self):
        if self.get_doc_before_save():
            frappe.throw("运行事件不可改写")

    def on_trash(self):
        frappe.throw("运行事件不可删除")
