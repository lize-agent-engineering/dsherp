import frappe
from frappe.model.document import Document


class DSConfigurationTransfer(Document):
    def validate(self):
        previous=self.get_doc_before_save()
        if previous and any(self.get(key)!=previous.get(key) for key in ('owner','request_id','bundle','payload','platform_grant')):
            frappe.throw('配置交接绑定不可修改')

    def on_trash(self):
        # Ruling #3: an audit record is an external promise. Permissions can be bypassed
        # (ignore_permissions, Administrator); this cannot.
        frappe.throw("配置交接记录不可删除")
