import frappe
from frappe.model.document import Document


class DSBusinessCredential(Document):
    """The window one business user's native API key is allowed to live in.

    The secret itself stays where Frappe keeps it (User.api_secret); this row only says
    which key was issued, when, and until when - so an expired key can be refused instead
    of being valid forever."""

    def validate(self):
        from dsherp_bridge import business_credentials as policy
        if policy.moment(self.expires_at) <= policy.moment(self.issued_at):
            frappe.throw('凭据的失效时间必须晚于签发时间')
        previous = self.get_doc_before_save()
        if previous and previous.user != self.user:
            frappe.throw('凭据记录不可改绑到另一个用户')
        if previous and int(self.version) < int(previous.version):
            frappe.throw('签发次数只增不减')
