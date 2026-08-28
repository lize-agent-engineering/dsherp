import frappe
from frappe.model.document import Document


class DSConfigurationConfirmation(Document):
    def validate(self):
        previous=self.get_doc_before_save()
        if previous and any(self.get(key)!=previous.get(key) for key in ('owner','bundle','payload','digest','expires_at')):
            frappe.throw('配置确认不可修改，请重新确认新配置包')
