import frappe
from frappe.model.document import Document


class DSConfigurationExecution(Document):
    def validate(self):
        previous=self.get_doc_before_save()
        if previous and (previous.status!='Running' or any(self.get(key)!=previous.get(key) for key in ('owner','confirmation','request_id'))):
            frappe.throw('已记录的配置执行结果不可改写')

    def on_trash(self):
        # Ruling #3: an audit record is an external promise. Permissions can be bypassed
        # (ignore_permissions, Administrator); this cannot.
        frappe.throw("配置执行结果不可删除")
