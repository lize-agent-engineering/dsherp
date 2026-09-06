import frappe
from frappe.model.document import Document

# A run that has ended is a fact about what the assistant did. Nothing on the ordinary
# document path may rewrite it - not a role, not Administrator, not ignore_permissions.
TERMINAL = ('Succeeded', 'Failed', 'Cancelled')
# The one transition a person makes through the document path: asking for a stop. Every
# other change to a run is made by the execution module itself through database writes,
# which this validation does not see and which the state machine there constrains.
CANCEL = {('Queued', 'Cancelled'), ('Running', 'Cancelling')}
CANCEL_FIELDS = {'status', 'cancel_request_id'}
# Standard bookkeeping Frappe rewrites on every save; not a change to the fact.
BOOKKEEPING = {'modified', 'modified_by', 'docstatus', 'idx', '_user_tags', '_comments', '_assign', '_liked_by'}


class DSModelRun(Document):
    def validate(self):
        previous = self.get_doc_before_save()
        if previous is None:
            return
        if previous.status in TERMINAL:
            # Ruling #3 / G7: a finished run is an external promise. track_changes records a
            # rewrite; it does not prevent one. This does.
            frappe.throw('运行已结束，其记录不可改写：它是助手做过什么的凭据')
        changed = {field.fieldname for field in self.meta.fields
                   if (self.get(field.fieldname) or None) != (previous.get(field.fieldname) or None)}
        changed -= BOOKKEEPING
        if (previous.status, self.status) in CANCEL and changed <= CANCEL_FIELDS:
            return
        frappe.throw('运行记录只能由服务端按状态机推进；这条路径只允许用户请求取消')

    def on_trash(self):
        # Ruling #3: an audit record is an external promise. Permissions can be bypassed
        # (ignore_permissions, Administrator); this cannot.
        frappe.throw('运行记录不可删除：它是助手做过什么的凭据')
