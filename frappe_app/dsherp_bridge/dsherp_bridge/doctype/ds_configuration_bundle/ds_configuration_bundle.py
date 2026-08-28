import frappe
from frappe.model.document import Document


class DSConfigurationBundle(Document):
    def validate(self):
        previous=self.get_doc_before_save()
        if previous and any(self.get(key)!=previous.get(key) for key in ('owner','conversation','payload','digest','baseline')):
            frappe.throw('配置包不可修改，请生成新配置包')
