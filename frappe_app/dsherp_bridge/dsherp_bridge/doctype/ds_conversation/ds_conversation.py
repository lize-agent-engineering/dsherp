import frappe
from frappe.model.document import Document

class DSConversation(Document):
    
    def on_trash(self):
        # A conversation is the user's own thread and may be archived or emptied of
        # personal content (ruling #10). It may not be removed while the runs it
        # explains are still there: that would orphan the audit trail.
        runs = frappe.db.count('DS Model Run', {'conversation': self.name})
        if runs:
            frappe.throw(f'会话 {self.name} 还有 {runs} 条运行记录，删除会让审计事实失去上下文；'
                         '要清除个人内容请用 delete-user-data')
