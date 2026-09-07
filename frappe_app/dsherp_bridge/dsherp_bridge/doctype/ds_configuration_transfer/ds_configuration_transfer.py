import frappe
from frappe.model.document import Document

# The binding, the owner and the window are one immutable promise; only a new transfer may
# carry a different one. The platform authorization is not here any more (plan 5): it lives
# in the Site cache for exactly this window (dsherp_bridge.grants.stash_transfer).
FROZEN=('owner','request_id','bundle','payload','expires_at')


class DSConfigurationTransfer(Document):
    def validate(self):
        previous=self.get_doc_before_save()
        if previous and any(self.get(key)!=previous.get(key) for key in FROZEN):
            frappe.throw('配置交接绑定不可修改')

    def on_trash(self):
        # Ruling #3: an audit record is an external promise. Permissions can be bypassed
        # (ignore_permissions, Administrator); this cannot.
        frappe.throw("配置交接记录不可删除")
