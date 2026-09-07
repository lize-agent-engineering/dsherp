import frappe
from frappe.model.document import Document


class DSDoctypePolicy(Document):
    def validate(self):
        # A policy decides what the assistant may write; a change to it is a change to
        # the blast radius, so it never lands without a stated reason (ruling #3).
        if not (self.change_reason or '').strip():
            frappe.throw('修改执行策略必须说明原因')
        previous = self.get_doc_before_save()
        if previous and (previous.change_reason or '') == (self.change_reason or ''):
            frappe.throw('这次修改需要新的原因，不能沿用上一次的说明')
        if not frappe.db.exists('DocType', self.target_doctype):
            frappe.throw('策略目标 DocType 不存在：' + self.target_doctype)
        for route in self.routes:
            if not frappe.db.exists('DocType', route.target_doctype):
                frappe.throw('策略路由目标 DocType 不存在：' + route.target_doctype)
