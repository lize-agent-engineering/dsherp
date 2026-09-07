import hashlib
import frappe
from frappe.model.document import Document

# What a binding is. A change to any of these is a change of binding and bumps the version;
# the credential columns on the same row are not part of it, so a renewal does not.
BINDING_FIELDS = ("enterprise", "platform_user", "erp_user", "enabled")


def active_key(enterprise, erp_user):
    return f"{enterprise}\0{erp_user}"


class DSMembership(Document):
    def autoname(self):
        self.name = hashlib.sha256((self.enterprise + "\0" + self.platform_user).encode()).hexdigest()[:32]

    def validate(self):
        previous = self.get_doc_before_save()
        if previous and (previous.enterprise != self.enterprise or previous.platform_user != self.platform_user):
            frappe.throw("Membership identity cannot be changed; disable and create a new binding")
        self._sole_holder_of_the_business_user()
        self._count_the_binding(previous)

    def _sole_holder_of_the_business_user(self):
        """A business user answers to one platform member.

        The business Site keeps a single api_secret per user, so two enabled memberships on
        the same erp_user would take turns renewing and invalidating each other's credential.
        The guarantee is the unique index on `active_binding`: two transactions inserting the
        same enabled binding both pass a read-then-write check, and the second one has to fail
        at the database. The lookup below only exists to say so in words before that happens."""
        self.active_binding = active_key(self.enterprise, self.erp_user) if self.enabled else None
        if not self.enabled:
            return
        clash = frappe.db.get_value("DS Membership", {
            "active_binding": self.active_binding, "name": ("!=", self.name or ""),
        }, "platform_user")
        if clash:
            frappe.throw(f"业务用户 {self.erp_user} 已绑定到平台成员 {clash}；"
                         "一个业务用户只能有一个启用中的绑定，先停用原绑定再新建")

    def _count_the_binding(self, previous):
        """An integer that goes up when the binding changes and only then.

        A grant carries this number; the business Site refuses a grant whose number no longer
        matches. A content hash would hand a disabled-then-re-enabled binding its old number
        back, and an old grant with it (R8). Renewing the borrowed credential leaves it alone."""
        if previous is None:
            self.binding_version = 1
            return
        if any(self.get(field) != previous.get(field) for field in BINDING_FIELDS):
            self.binding_version = int(previous.binding_version or 0) + 1
        else:
            self.binding_version = int(previous.binding_version or 1)

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
