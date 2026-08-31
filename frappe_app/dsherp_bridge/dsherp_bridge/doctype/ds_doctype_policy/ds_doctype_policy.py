import frappe
from frappe.model.document import Document


class DSDoctypePolicy(Document):
    def validate(self):
        if not frappe.db.exists('DocType', self.target_doctype):
            frappe.throw('策略目标 DocType 不存在：' + self.target_doctype)
        for route in self.routes:
            if not frappe.db.exists('DocType', route.target_doctype):
                frappe.throw('策略路由目标 DocType 不存在：' + route.target_doctype)
