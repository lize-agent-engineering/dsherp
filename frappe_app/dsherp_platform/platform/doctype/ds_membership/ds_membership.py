import hashlib
import frappe
from frappe.model.document import Document

class DSMembership(Document):
    def autoname(self):
        self.name = hashlib.sha256((self.enterprise + "\0" + self.platform_user).encode()).hexdigest()[:32]

    def validate(self):
        previous = self.get_doc_before_save()
        if previous and (previous.enterprise != self.enterprise or previous.platform_user != self.platform_user):
            frappe.throw("Membership identity cannot be changed; disable and create a new binding")
        self._sole_holder_of_the_business_user()

    def _sole_holder_of_the_business_user(self):
        """A business user answers to one platform member.

        The business Site keeps a single api_secret per user, so two enabled memberships on
        the same erp_user would take turns renewing and invalidating each other's credential.
        The binding refuses that instead of letting logins fight."""
        if not self.enabled:
            return
        clash = frappe.db.get_value("DS Membership", {
            "enterprise": self.enterprise, "erp_user": self.erp_user, "enabled": 1,
            "name": ("!=", self.name or ""),
        }, "platform_user")
        if clash:
            frappe.throw(f"业务用户 {self.erp_user} 已绑定到平台成员 {clash}；"
                         "一个业务用户只能有一个启用中的绑定，先停用原绑定再新建")

    def on_update(self):
        """Disabling a membership takes back the credential it lent out.

        Stopping platform reads is not enough: the key itself is out there until its window
        closes. The business Site is asked to kill it now, and an unreachable Site is
        recorded rather than reported as a revocation that happened."""
        previous = self.get_doc_before_save()
        if previous and previous.enabled and not self.enabled:
            from dsherp_platform.api import revoke_credential
            outcome = revoke_credential(self)
            if not outcome.get("revoked"):
                frappe.msgprint(f"业务凭据未能立即吊销（{outcome.get('why') or outcome.get('status')}）；"
                                "它仍会在有效期结束时失效，请确认业务站可达后复查")

    def on_trash(self):
        if self.enabled:
            frappe.throw("先停用该绑定再删除，否则借出的业务凭据不会被吊销")
