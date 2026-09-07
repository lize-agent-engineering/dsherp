import frappe
from frappe.model.document import Document


class DSConfigurationBundle(Document):
    def validate(self):
        previous=self.get_doc_before_save()
        if previous and any(self.get(key)!=previous.get(key) for key in ('owner','conversation','source_transfer','payload','digest','baseline')):
            frappe.throw('配置包不可修改，请生成新配置包')

    def on_trash(self):
        # Ruling #3: an audit record is an external promise. Permissions can be bypassed
        # (ignore_permissions, Administrator); this cannot.
        frappe.throw("配置包不可删除：预览与发布都以它为准")
