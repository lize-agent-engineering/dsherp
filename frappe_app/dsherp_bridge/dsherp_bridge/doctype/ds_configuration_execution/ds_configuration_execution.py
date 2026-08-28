import frappe
from frappe.model.document import Document


class DSConfigurationExecution(Document):
    def validate(self):
        previous=self.get_doc_before_save()
        if previous and (previous.status!='Running' or any(self.get(key)!=previous.get(key) for key in ('owner','confirmation','request_id'))):
            frappe.throw('已记录的配置执行结果不可改写')
